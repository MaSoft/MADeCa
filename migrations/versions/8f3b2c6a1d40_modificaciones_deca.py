"""modificaciones_deca (Apartado Quinto, método 1)

Revision ID: 8f3b2c6a1d40
Revises: 5c1e9a7d3b20
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8f3b2c6a1d40"
down_revision: str | None = "5c1e9a7d3b20"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "modificaciones_deca",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("expedicion_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("expediciones.id"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("numero", sa.Integer, nullable=False),
        sa.Column("fecha", sa.DateTime(timezone=True), nullable=False),
        sa.Column("motivo", sa.Text, nullable=False),
        sa.Column("cambios", postgresql.JSONB, nullable=False),
        sa.Column("ruta_pdf_anterior", sa.String(500), nullable=False),
        sa.Column("origen", sa.String(200), nullable=False),
    )
    op.create_index("ix_modificaciones_deca_expedicion_id", "modificaciones_deca", ["expedicion_id"])


def downgrade() -> None:
    op.drop_index("ix_modificaciones_deca_expedicion_id", table_name="modificaciones_deca")
    op.drop_table("modificaciones_deca")
