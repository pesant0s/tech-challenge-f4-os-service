"""esquema inicial do OS Service
Revision ID: 0001 · Revises: base"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUS_OS = (
    "ABERTA", "AGUARDANDO_APROVACAO", "AGUARDANDO_PAGAMENTO", "NA_FILA",
    "EM_DIAGNOSTICO", "EM_EXECUCAO", "FINALIZADA", "ENTREGUE", "NEGADA", "CANCELADA",
)


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.Enum("ADMIN", "ATENDENTE", name="roleenum"), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usuarios_username", "usuarios", ["username"], unique=True)

    op.create_table(
        "clientes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nome", sa.String(150), nullable=False),
        sa.Column("cpf_cnpj", sa.String(18), nullable=False),
        sa.Column("email", sa.String(150), nullable=True),
        sa.Column("telefone", sa.String(20), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cpf_cnpj"),
    )

    op.create_table(
        "veiculos",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("placa", sa.String(10), nullable=False),
        sa.Column("marca", sa.String(60), nullable=False),
        sa.Column("modelo", sa.String(60), nullable=False),
        sa.Column("ano", sa.String(4), nullable=False),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["cliente_id"], ["clientes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("placa"),
    )
    op.create_index("ix_veiculos_cliente_id", "veiculos", ["cliente_id"])

    op.create_table(
        "ordens_servico",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("veiculo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Enum(*STATUS_OS, name="statusos"), nullable=False),
        sa.Column("valor_total", sa.Numeric(10, 2), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["cliente_id"], ["clientes.id"]),
        sa.ForeignKeyConstraint(["veiculo_id"], ["veiculos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("valor_total IS NULL OR valor_total >= 0", name="ck_ordens_servico_valor_nao_negativo"),
    )
    op.create_index("ix_ordens_servico_cliente_id", "ordens_servico", ["cliente_id"])
    op.create_index("ix_ordens_servico_status", "ordens_servico", ["status"])

    op.create_table(
        "itens_os",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("os_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", sa.Enum("SERVICO", "PECA", name="tipoitem"), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("descricao", sa.String(150), nullable=True),
        sa.Column("preco_unitario", sa.Numeric(10, 2), nullable=True),
        sa.ForeignKeyConstraint(["os_id"], ["ordens_servico.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantidade > 0", name="ck_itens_os_quantidade_positiva"),
        sa.CheckConstraint("preco_unitario IS NULL OR preco_unitario >= 0", name="ck_itens_os_preco_nao_negativo"),
    )
    op.create_index("ix_itens_os_os_id", "itens_os", ["os_id"])

    op.create_table(
        "historico_status_os",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("os_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", postgresql.ENUM(name="statusos", create_type=False), nullable=False),
        sa.Column("entrou_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["os_id"], ["ordens_servico.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_historico_status_os_os_id", "historico_status_os", ["os_id"])


def downgrade() -> None:
    op.drop_index("ix_historico_status_os_os_id", table_name="historico_status_os")
    op.drop_table("historico_status_os")
    op.drop_index("ix_itens_os_os_id", table_name="itens_os")
    op.drop_table("itens_os")
    op.drop_index("ix_ordens_servico_status", table_name="ordens_servico")
    op.drop_index("ix_ordens_servico_cliente_id", table_name="ordens_servico")
    op.drop_table("ordens_servico")
    op.drop_index("ix_veiculos_cliente_id", table_name="veiculos")
    op.drop_table("veiculos")
    op.drop_table("clientes")
    op.drop_index("ix_usuarios_username", table_name="usuarios")
    op.drop_table("usuarios")
    op.execute("DROP TYPE IF EXISTS tipoitem")
    op.execute("DROP TYPE IF EXISTS statusos")
    op.execute("DROP TYPE IF EXISTS roleenum")
