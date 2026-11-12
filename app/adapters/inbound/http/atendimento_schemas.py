from uuid import UUID
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.domain.entities.os import StatusOS, TipoItem
from app.domain.saga import EstadoSaga, PassoSaga


class ServicoItemCreate(BaseModel):
    servico_id: UUID
    quantidade: int = Field(default=1, ge=1)


class PecaItemCreate(BaseModel):
    peca_id: UUID
    quantidade: int = Field(default=1, ge=1)


class ItemOSResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tipo: TipoItem
    item_id: UUID
    quantidade: int
    descricao: str | None = None
    preco_unitario: Decimal | None = None


class HistoricoStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: StatusOS
    entrou_em: datetime


class OSCreate(BaseModel):
    cliente_id: UUID
    veiculo_id: UUID
    servicos: list[ServicoItemCreate] = Field(default_factory=list)
    pecas: list[PecaItemCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def deve_ter_ao_menos_um_item(self):
        if not self.servicos and not self.pecas:
            raise ValueError("A OS deve ter ao menos um serviço ou uma peça")
        return self


class SagaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    passo: PassoSaga
    estado: EstadoSaga
    motivo: str | None = None
    compensacoes: list[str]
    expira_em: datetime | None = None


class OSResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cliente_id: UUID
    veiculo_id: UUID
    status: StatusOS
    valor_total: Decimal | None = None
    criado_em: datetime
    iniciado_em: datetime | None
    finalizado_em: datetime | None
    itens: list[ItemOSResponse]
    historico: list[HistoricoStatusResponse]
    saga: SagaResponse | None = None

