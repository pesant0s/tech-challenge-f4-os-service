"""Rotas para outros serviços, fora do API Gateway: a Lambda de CPF consulta o cliente aqui (ADR-022)."""
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.outbound.persistence.cliente_repository import ClienteRepositoryAdapter
from app.domain.exceptions import BusinessRuleException, NotFoundException
from app.domain.value_objects.documentos import CpfCnpj
from app.infrastructure.config import settings
from app.infrastructure.database import get_db

router = APIRouter(prefix="/interno", tags=["🔒 Interno"])


class AutenticarClienteRequest(BaseModel):
    cpf_cnpj: str


class ClienteAutenticadoResponse(BaseModel):
    id: UUID
    nome: str
    cpf_cnpj: str
    ativo: bool


def exigir_token_interno(x_token_interno: str | None = Header(default=None)) -> None:
    """Sem TOKEN_INTERNO configurado as rotas internas ficam fechadas."""
    esperado = settings.TOKEN_INTERNO
    if not esperado or not x_token_interno or not hmac.compare_digest(x_token_interno.encode(), esperado.encode()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a serviços internos")


@router.post("/clientes/autenticar", response_model=ClienteAutenticadoResponse,
    summary="Cliente pelo CPF/CNPJ (uso da Lambda de autenticação)",
    description="Exige o cabeçalho `X-Token-Interno`. Responde também cliente inativo: quem decide o acesso é a Lambda.")
def autenticar_cliente(dados: AutenticarClienteRequest, db: Session = Depends(get_db), _=Depends(exigir_token_interno)):
    try:
        digitos = CpfCnpj.from_str(dados.cpf_cnpj).digits
    except ValueError as exc:
        raise BusinessRuleException(str(exc))
    cliente = ClienteRepositoryAdapter(db).buscar_por_cpf_cnpj_digits(digitos)
    if not cliente:
        raise NotFoundException("Cliente não encontrado")
    return cliente
