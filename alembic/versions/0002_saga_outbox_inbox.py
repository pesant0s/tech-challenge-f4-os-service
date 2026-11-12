"""estado da Saga, outbox e inbox da mensageria
Revision ID: 0002 · Revises: 0001"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PASSOS = ("RESERVANDO_PECAS", "GERANDO_ORCAMENTO", "AGUARDANDO_APROVACAO",
          "AGUARDANDO_PAGAMENTO", "ENFILEIRANDO", "EM_EXECUCAO")
ESTADOS = ("EM_ANDAMENTO", "COMPENSANDO", "CONCLUIDA", "COMPENSADA", "FALHA_NA_COMPENSACAO")
AGORA = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "saga_instancia",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("os_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ordens_servico.id"), nullable=False),
        sa.Column("passo", sa.Enum(*PASSOS, name="passosaga"), nullable=False),
        sa.Column("estado", sa.Enum(*ESTADOS, name="estadosaga"), nullable=False),
        sa.Column("motivo", sa.String(40), nullable=True),
        sa.Column("compensacoes", sa.JSON(), nullable=False),
        sa.Column("reserva_feita", sa.Boolean(), nullable=False),
        sa.Column("orcamento_id", sa.String(64), nullable=True),
        sa.Column("orcamento_ativo", sa.Boolean(), nullable=False),
        sa.Column("pagamento_id", sa.String(64), nullable=True),
        sa.Column("pagamento_confirmado", sa.Boolean(), nullable=False),
        sa.Column("tentativas", sa.Integer(), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criada_em", sa.DateTime(timezone=True), server_default=AGORA, nullable=True),
        sa.Column("atualizada_em", sa.DateTime(timezone=True), server_default=AGORA, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("os_id"),
    )
    op.create_index("ix_saga_instancia_estado", "saga_instancia", ["estado"])
    op.create_index("ix_saga_instancia_expira_em", "saga_instancia", ["expira_em"])

    op.create_table(
        "outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exchange", sa.String(60), nullable=False),
        sa.Column("rota", sa.String(80), nullable=False),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("corpo", sa.JSON(), nullable=False),
        sa.Column("cabecalhos", sa.JSON(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=AGORA, nullable=False),
        sa.Column("publicado_em", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_pendentes", "outbox", ["publicado_em", "criado_em"])

    op.create_table(
        "mensagens_processadas",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("processada_em", sa.DateTime(timezone=True), server_default=AGORA, nullable=False),
        sa.PrimaryKeyConstraint("message_id"),
    )


def downgrade() -> None:
    op.drop_table("mensagens_processadas")
    op.drop_index("ix_outbox_pendentes", table_name="outbox")
    op.drop_table("outbox")
    op.drop_index("ix_saga_instancia_expira_em", table_name="saga_instancia")
    op.drop_index("ix_saga_instancia_estado", table_name="saga_instancia")
    op.drop_table("saga_instancia")
    sa.Enum(name="estadosaga").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="passosaga").drop(op.get_bind(), checkfirst=True)
