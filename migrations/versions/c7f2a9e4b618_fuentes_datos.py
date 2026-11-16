"""fuentes de datos automáticas (conectores)

Revision ID: c7f2a9e4b618
Revises: b4e8c1d9a372
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7f2a9e4b618"
down_revision: str | None = "b4e8c1d9a372"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fuentes_datos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("nombre", sa.String(150), nullable=False),
        sa.Column("config_cifrada", sa.Text, nullable=False),
        sa.Column("estado_sync", postgresql.JSONB, nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("intervalo_minutos", sa.Integer, nullable=False, server_default="15"),
        sa.Column("ultima_ejecucion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_resultado", sa.String(300), nullable=True),
        sa.Column("ultimo_error", sa.Text, nullable=True),
        sa.Column("fecha_alta", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fuentes_datos_tenant_id", "fuentes_datos", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_fuentes_datos_tenant_id", table_name="fuentes_datos")
    op.drop_table("fuentes_datos")
