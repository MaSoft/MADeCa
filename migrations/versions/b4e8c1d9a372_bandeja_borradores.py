"""bandeja de revisión: borradores_deca + autorización de OCR de terceros

Revision ID: b4e8c1d9a372
Revises: a1d7e4f2c915
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4e8c1d9a372"
down_revision: str | None = "a1d7e4f2c915"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("ocr_tercero_autorizado", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("tenants", sa.Column("ocr_tercero_autorizado_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("ocr_tercero_autorizado_por", sa.String(320), nullable=True))
    op.create_table(
        "borradores_deca",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("origen", sa.String(100), nullable=False),
        sa.Column("referencia_fuente", sa.String(500), nullable=False),
        sa.Column("nombre_documento", sa.String(300), nullable=True),
        sa.Column("ruta_documento", sa.String(500), nullable=True),
        sa.Column("datos", postgresql.JSONB, nullable=False),
        sa.Column("confianza", postgresql.JSONB, nullable=False),
        sa.Column("avisos", postgresql.JSONB, nullable=False),
        sa.Column("motor_ocr", sa.String(30), nullable=True),
        sa.Column("fecha_alta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecha_resolucion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resuelto_por", sa.String(320), nullable=True),
        sa.Column("expedicion_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("expediciones.id"), nullable=True),
        sa.UniqueConstraint("tenant_id", "origen", "referencia_fuente", name="uq_borrador_fuente"),
    )
    op.create_index("ix_borrador_tenant_estado", "borradores_deca", ["tenant_id", "estado"])


def downgrade() -> None:
    op.drop_index("ix_borrador_tenant_estado", table_name="borradores_deca")
    op.drop_table("borradores_deca")
    op.drop_column("tenants", "ocr_tercero_autorizado_por")
    op.drop_column("tenants", "ocr_tercero_autorizado_en")
    op.drop_column("tenants", "ocr_tercero_autorizado")
