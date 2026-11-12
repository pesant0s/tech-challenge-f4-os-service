from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.infrastructure.database import get_db
from app.infrastructure.security import (
    get_current_cliente, get_current_user, get_escopo_cliente, require_admin,
)
from app.adapters.inbound.http.atendimento_schemas import OSCreate, OSResponse
from app.adapters.outbound.persistence.os_repository import OSRepositoryAdapter
from app.adapters.outbound.persistence.cliente_repository import ClienteRepositoryAdapter
from app.application.use_cases.criar_os import CriarOSUseCase
from app.application.use_cases.entregar_os import EntregarOSUseCase
from app.application.use_cases.listar_os import ListarOSUseCase
from app.application.use_cases.obter_os import ObterOSUseCase
from app.domain.entities.os import StatusOS

router = APIRouter(prefix="/atendimento", tags=["📋 Ordens de Serviço"])


def criar_os_use_case(db: Session = Depends(get_db)) -> CriarOSUseCase:
    return CriarOSUseCase(os_repo=OSRepositoryAdapter(db), cliente_repo=ClienteRepositoryAdapter(db))


@router.post("/os", response_model=OSResponse, status_code=201,
    summary="Criar Ordem de Serviço",
    description="Cria uma OS com status inicial **ABERTA**. Os itens entram sem preço; a precificação vem do orçamento.")
def criar_os(data: OSCreate, caso_de_uso: CriarOSUseCase = Depends(criar_os_use_case), _=Depends(get_current_user)):
    return caso_de_uso.executar(data.cliente_id, data.veiculo_id, data.servicos, data.pecas)


@router.get("/os", response_model=list[OSResponse], summary="Listar todas as OS")
def listar_os(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status: StatusOS | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return ListarOSUseCase(OSRepositoryAdapter(db)).executar(skip, limit, status)


@router.get("/os/metricas/tempo-medio",
    summary="Tempo médio de execução e por status",
    description="Tempo médio (em minutos) entre início e finalização e, pelo histórico, em cada status. **Requer ADMIN.**")
def tempo_medio(db: Session = Depends(get_db), _=Depends(require_admin)):
    return OSRepositoryAdapter(db).tempo_medio_execucao()


@router.get("/os/consulta", response_model=list[OSResponse],
    summary="Minhas ordens de serviço (cliente)",
    description="""
Requer **token de cliente**, obtido em `POST /auth/cpf`. Devolve apenas as ordens de
serviço de quem está autenticado — o CPF vem do token, não da requisição, então não
há como consultar as ordens de outra pessoa.
""")
def consultar_minhas_os(db: Session = Depends(get_db), cliente=Depends(get_current_cliente)):
    return OSRepositoryAdapter(db).buscar_por_cliente_id(cliente.id)


@router.get("/os/{os_id}", response_model=OSResponse,
    summary="Consultar OS por ID",
    description="""
Aceita **token de funcionário ou de cliente**. Funcionário consulta qualquer OS; cliente,
apenas as próprias. A OS de outro cliente responde `404`, idêntico a uma OS inexistente.
""")
def obter_os(os_id: UUID, db: Session = Depends(get_db), cliente_id=Depends(get_escopo_cliente)):
    return ObterOSUseCase(OSRepositoryAdapter(db)).executar(os_id, cliente_id=cliente_id)


@router.post("/os/{os_id}/entregar", response_model=OSResponse,
    summary="Entregar veículo",
    description="Move a OS de `FINALIZADA` → `ENTREGUE`. Requer **token de funcionário**.")
def entregar_os(os_id: UUID, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return EntregarOSUseCase(OSRepositoryAdapter(db)).executar(os_id)
