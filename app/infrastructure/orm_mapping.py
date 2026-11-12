"""Mapeamento imperativo: as entidades de domínio seguem sem nenhum import de SQLAlchemy."""
import uuid
from datetime import timezone
from sqlalchemy import (
    Table, Column, String, Integer, Numeric, Boolean, DateTime, ForeignKey,
    Enum, CheckConstraint, Index, JSON, TypeDecorator,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func, text

from app.infrastructure.database import mapper_registry, metadata
from app.domain.entities.usuario import Usuario, RoleEnum
from app.domain.entities.cliente import Cliente, Veiculo
from app.domain.entities.os import OrdemDeServico, ItemOS, HistoricoStatusOS, StatusOS, TipoItem
from app.domain.saga import EstadoSaga, PassoSaga, SagaAberturaOS


class DataHoraUTC(TypeDecorator):
    """timestamptz sempre devolvido em UTC; o SQLite dos testes perde o fuso na leitura."""

    impl = DateTime
    cache_ok = True

    def process_result_value(self, valor, dialect):
        if valor is None:
            return None
        return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)


usuarios = Table(
    "usuarios", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("username", String(50), unique=True, nullable=False, index=True),
    Column("hashed_password", String(255), nullable=False),
    Column("role", Enum(RoleEnum), nullable=False, default=RoleEnum.ATENDENTE),
    Column("ativo", Boolean, nullable=False, default=True),
)

clientes = Table(
    "clientes", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("nome", String(150), nullable=False),
    Column("cpf_cnpj", String(18), nullable=False, unique=True),
    Column("email", String(150), nullable=True),
    Column("telefone", String(20), nullable=False),
    Column("ativo", Boolean, nullable=False, default=True, server_default=text("true")),
)

veiculos = Table(
    "veiculos", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("placa", String(10), nullable=False, unique=True),
    Column("marca", String(60), nullable=False),
    Column("modelo", String(60), nullable=False),
    Column("ano", String(4), nullable=False),
    Column("cliente_id", UUID(as_uuid=True), ForeignKey("clientes.id"), nullable=False, index=True),
)

ordens_servico = Table(
    "ordens_servico", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("cliente_id", UUID(as_uuid=True), ForeignKey("clientes.id"), nullable=False, index=True),
    Column("veiculo_id", UUID(as_uuid=True), ForeignKey("veiculos.id"), nullable=False),
    Column("status", Enum(StatusOS), nullable=False, default=StatusOS.ABERTA, index=True),
    Column("valor_total", Numeric(10, 2), nullable=True),
    Column("criado_em", DataHoraUTC(timezone=True), server_default=func.now()),
    Column("iniciado_em", DataHoraUTC(timezone=True), nullable=True),
    Column("finalizado_em", DataHoraUTC(timezone=True), nullable=True),
    CheckConstraint("valor_total IS NULL OR valor_total >= 0", name="ck_ordens_servico_valor_nao_negativo"),
)

itens_os = Table(
    "itens_os", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("os_id", UUID(as_uuid=True), ForeignKey("ordens_servico.id"), nullable=False, index=True),
    Column("tipo", Enum(TipoItem), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("quantidade", Integer, nullable=False),
    Column("descricao", String(150), nullable=True),
    Column("preco_unitario", Numeric(10, 2), nullable=True),
    CheckConstraint("quantidade > 0", name="ck_itens_os_quantidade_positiva"),
    CheckConstraint("preco_unitario IS NULL OR preco_unitario >= 0", name="ck_itens_os_preco_nao_negativo"),
)

historico_status_os = Table(
    "historico_status_os", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("os_id", UUID(as_uuid=True), ForeignKey("ordens_servico.id"), nullable=False, index=True),
    Column("status", Enum(StatusOS), nullable=False),
    Column("entrou_em", DataHoraUTC(timezone=True), nullable=False),
)

saga_instancia = Table(
    "saga_instancia", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("os_id", UUID(as_uuid=True), ForeignKey("ordens_servico.id"), nullable=False, unique=True),
    Column("passo", Enum(PassoSaga), nullable=False),
    Column("estado", Enum(EstadoSaga), nullable=False, index=True),
    Column("motivo", String(40), nullable=True),
    # MutableList: a Saga altera a lista no lugar (pop e append), e o ORM precisa perceber.
    Column("compensacoes", MutableList.as_mutable(JSON), nullable=False, default=list),
    Column("reserva_feita", Boolean, nullable=False, default=False),
    Column("orcamento_id", String(64), nullable=True),
    Column("orcamento_ativo", Boolean, nullable=False, default=False),
    Column("pagamento_id", String(64), nullable=True),
    Column("pagamento_confirmado", Boolean, nullable=False, default=False),
    Column("tentativas", Integer, nullable=False, default=0),
    Column("expira_em", DataHoraUTC(timezone=True), nullable=True, index=True),
    Column("criada_em", DataHoraUTC(timezone=True), server_default=func.now()),
    Column("atualizada_em", DataHoraUTC(timezone=True), server_default=func.now(), onupdate=func.now()),
)

# Outbox e inbox não são entidades de domínio: só tabelas da mensageria (ADR-021).
outbox = Table(
    "outbox", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("exchange", String(60), nullable=False),
    Column("rota", String(80), nullable=False),
    Column("tipo", String(40), nullable=False),
    Column("corpo", JSON, nullable=False),
    Column("cabecalhos", JSON, nullable=False),
    Column("criado_em", DataHoraUTC(timezone=True), nullable=False, server_default=func.now()),
    Column("publicado_em", DataHoraUTC(timezone=True), nullable=True),
    Index("ix_outbox_pendentes", "publicado_em", "criado_em"),
)

mensagens_processadas = Table(
    "mensagens_processadas", metadata,
    Column("message_id", UUID(as_uuid=True), primary_key=True),
    Column("tipo", String(40), nullable=False),
    Column("processada_em", DataHoraUTC(timezone=True), nullable=False, server_default=func.now()),
)

mapper_registry.map_imperatively(Usuario, usuarios)

mapper_registry.map_imperatively(Cliente, clientes, properties={
    "veiculos": relationship(Veiculo, back_populates="cliente"),
})
mapper_registry.map_imperatively(Veiculo, veiculos, properties={
    "cliente": relationship(Cliente, back_populates="veiculos"),
})

mapper_registry.map_imperatively(HistoricoStatusOS, historico_status_os)
mapper_registry.map_imperatively(OrdemDeServico, ordens_servico, properties={
    "itens": relationship(ItemOS, back_populates="os", lazy="selectin"),
    "historico": relationship(HistoricoStatusOS, order_by=historico_status_os.c.entrou_em, lazy="selectin"),
    "saga": relationship(SagaAberturaOS, uselist=False, lazy="selectin", viewonly=True),
})
mapper_registry.map_imperatively(SagaAberturaOS, saga_instancia)
mapper_registry.map_imperatively(ItemOS, itens_os, properties={
    "os": relationship(OrdemDeServico, back_populates="itens"),
})
