import logging
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.infrastructure.config import settings
from app.infrastructure.logging_config import ConsoleFormatter, JsonFormatter, obter_correlation_id
from app.infrastructure.security import create_access_token, pwd_context
from app.infrastructure.database import get_db
from app.adapters.inbound.http.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware
from app.adapters.inbound.http.auth_schemas import UsuarioCreate
from app.adapters.inbound.http.cadastro_routes import router as cadastro_router
from app.adapters.inbound.http.atendimento_routes import router as atendimento_router
from app.adapters.inbound.http.auth_routes import router as auth_router
from app.adapters.outbound.persistence.auth_repository import create_usuario, get_usuario_by_username
from app.domain.entities.usuario import RoleEnum
from app.domain.exceptions import NotFoundException, ConflictException, BusinessRuleException

# Configurado no import para que os logs de startup já saiam no formato final.
_saida = logging.StreamHandler(sys.stdout)
_saida.setFormatter(JsonFormatter() if settings.LOG_FORMAT.lower() == "json" else ConsoleFormatter())
logging.basicConfig(handlers=[_saida], level=settings.LOG_LEVEL.upper(), force=True)
for _nome in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    logging.getLogger(_nome).handlers.clear()
    logging.getLogger(_nome).propagate = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    fabrica = app.dependency_overrides.get(get_db, get_db)
    db = next(fabrica())
    try:
        if not get_usuario_by_username(db, settings.ADMIN_USERNAME):
            create_usuario(db, UsuarioCreate(
                username=settings.ADMIN_USERNAME,
                password=settings.ADMIN_PASSWORD,
                role=RoleEnum.ADMIN,
            ))
    except IntegrityError:
        db.rollback()  # outro worker ou pod semeou o admin ao mesmo tempo
    finally:
        if get_db not in app.dependency_overrides:
            db.close()
    yield


# Atrás do API Gateway a origem vista é a VPC; o gateway repassa o IP do cliente em x-cliente-ip.
limiter = Limiter(key_func=lambda request: request.headers.get("x-cliente-ip") or get_remote_address(request))

app = FastAPI(
    title="Tech Challenge — Oficina · OS Service",
    description="""
Serviço de ordens de serviço da oficina: cadastro de clientes e veículos, abertura, consulta e entrega da OS.

## Autenticação

Dois tipos de token, ambos enviados como `Authorization: Bearer <token>`:

| Quem | Como obtém | Esquema no Authorize |
|---|---|---|
| **Funcionário** | `POST /auth/token` com usuário e senha | `OAuth2PasswordBearer` |
| **Cliente** | `POST /auth/cpf` com o CPF — Lambda publicada no API Gateway | `TokenCliente` |

O claim `tipo` do JWT separa os dois: token de cliente não abre as rotas de operação da
oficina, e token de funcionário não se passa por cliente.

## Papéis de funcionário

| Role | Descrição |
|---|---|
| `ADMIN` | Acesso total — gerencia usuários e acessa as métricas |
| `ATENDENTE` | Acesso operacional — clientes, veículos e ordens de serviço |

## Rotas do cliente

| Rota | Descrição |
|---|---|
| `GET /atendimento/os/consulta` | Ordens de serviço do cliente autenticado |
| `GET /atendimento/os/{id}` | Detalhe da OS — o cliente só vê as próprias |

## Acesso público

| Rota | Descrição |
|---|---|
| `GET /health` | Liveness |
| `GET /health/ready` | Readiness — confirma o banco |

## Ciclo de vida da OS

`ABERTA` → `AGUARDANDO_APROVACAO` → `AGUARDANDO_PAGAMENTO` → `NA_FILA` → `EM_DIAGNOSTICO` → `EM_EXECUCAO` → `FINALIZADA` → `ENTREGUE`

Transições alternativas: `NEGADA` (orçamento recusado) e `CANCELADA` (antes da fila). A entrega é a única
transição manual (`POST /atendimento/os/{id}/entregar`); as demais vêm dos eventos dos outros serviços.
    """,
    version="1.0.0",
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _erro(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "correlation_id": obter_correlation_id()})


@app.exception_handler(NotFoundException)
async def not_found_handler(request: Request, exc: NotFoundException):
    return _erro(404, exc.detail)


@app.exception_handler(ConflictException)
async def conflict_handler(request: Request, exc: ConflictException):
    return _erro(409, exc.detail)


@app.exception_handler(BusinessRuleException)
async def business_rule_handler(request: Request, exc: BusinessRuleException):
    logging.getLogger("oficina.negocio").warning("Regra de negócio violada: %s", exc.detail, extra={"regra": exc.detail})
    return _erro(422, exc.detail)


# O último middleware registrado é o mais externo: a correlação envolve todos os outros.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=False,  # o token vai no cabeçalho, não em cookie
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(auth_router)
app.include_router(cadastro_router)
app.include_router(atendimento_router)


@app.post("/auth/token", tags=["Auth"], summary="Obter token JWT")
@limiter.limit("10/minute")
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = get_usuario_by_username(db, form_data.username)
    if not user or not user.ativo or not pwd_context.verify(form_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")
    token = create_access_token({"sub": user.username, "role": user.role, "tipo": "usuario"})
    return {"access_token": token, "token_type": "bearer"}


@app.get("/health", tags=["Sistema"], summary="Liveness")
def health():
    """Sem I/O: uma queda do banco não deve fazer o Kubernetes reiniciar pods saudáveis."""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/health/ready", tags=["Sistema"], summary="Readiness")
def health_ready(db: Session = Depends(get_db)):
    """Confere o banco; sem ele o pod sai do balanceamento, mas segue vivo."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logging.getLogger("oficina.health").error("Readiness falhou: banco inacessível", exc_info=exc)
        return JSONResponse(status_code=503, content={
            "status": "degradado", "banco": "inacessivel", "correlation_id": obter_correlation_id(),
        })
    return {"status": "ok", "banco": "ok", "version": "1.0.0"}
