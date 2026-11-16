"""historial de cada expedición (quién y cuándo)

Revision ID: d3a8b5c2e710
Revises: c7f2a9e4b618
Create Date: 2026-10-11

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3a8b5c2e710"
down_revision: str | None = "c7f2a9e4b618"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eventos_expedicion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("expedicion_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("expediciones.id"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("fecha", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accion", sa.String(40), nullable=False),
        sa.Column("quien", sa.String(320), nullable=False),
        sa.Column("detalle", sa.Text, nullable=True),
    )
    op.create_index("ix_eventos_expedicion_expedicion_id", "eventos_expedicion", ["expedicion_id"])


def downgrade() -> None:
    op.drop_index("ix_eventos_expedicion_expedicion_id", table_name="eventos_expedicion")
    op.drop_table("eventos_expedicion")
