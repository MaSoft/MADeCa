"""esquema inicial

Revision ID: 47fab727da88
Revises:
Create Date: 2026-08-07

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "47fab727da88"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nombre", sa.String(300), nullable=False),
        sa.Column("nif_cif", sa.String(15), nullable=False, unique=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("fecha_alta", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "usuarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("rol", sa.String(20), nullable=False, server_default="operador"),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("fecha_alta", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "email", name="uq_usuario_tenant_email"),
    )

    op.create_table(
        "erp_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("nombre", sa.String(150), nullable=False),
        sa.Column("prefijo", sa.String(20), nullable=False),
        sa.Column("key_hash", sa.String(200), nullable=False),
        sa.Column("erp_origen", sa.String(30), nullable=False, server_default="generico"),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecha_ultimo_uso", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_erp_api_keys_tenant_id", "erp_api_keys", ["tenant_id"])

    op.create_table(
        "certificados",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("nombre", sa.String(150), nullable=False),
        sa.Column("ruta_archivo", sa.String(500), nullable=False),
        sa.Column("password_cifrado", sa.Text, nullable=False),
        sa.Column("titular_cn", sa.String(300), nullable=True),
        sa.Column("fecha_caducidad", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("fecha_subida", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_certificados_tenant_id", "certificados", ["tenant_id"])

    op.create_table(
        "expediciones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("referencia_externa", sa.String(200), nullable=False),
        sa.Column("erp_origen", sa.String(30), nullable=False, server_default="generico"),
        sa.Column("estado", sa.String(20), nullable=False, server_default="en_transito"),
        sa.Column("datos_deca", postgresql.JSONB, nullable=False),
        sa.Column("fecha_transporte", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecha_emision", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecha_finalizacion_servicio", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matricula_vehiculo", sa.String(15), nullable=False),
        sa.Column("matricula_remolque", sa.String(15), nullable=True),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("tamano_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("firmado", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("finalidad_contractual", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("certificado_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("certificados.id"), nullable=True),
        sa.Column("url_activa", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "tenant_id", "erp_origen", "referencia_externa", name="uq_expedicion_idempotencia"
        ),
    )
    op.create_index("ix_expedicion_tenant_fecha", "expediciones", ["tenant_id", "fecha_transporte"])

    op.create_table(
        "accesos_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("expedicion_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("expediciones.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("resultado", sa.String(30), nullable=False),
    )
    op.create_index("ix_accesos_log_expedicion_id", "accesos_log", ["expedicion_id"])


def downgrade() -> None:
    op.drop_table("accesos_log")
    op.drop_index("ix_expedicion_tenant_fecha", table_name="expediciones")
    op.drop_table("expediciones")
    op.drop_index("ix_certificados_tenant_id", table_name="certificados")
    op.drop_table("certificados")
    op.drop_index("ix_erp_api_keys_tenant_id", table_name="erp_api_keys")
    op.drop_table("erp_api_keys")
    op.drop_table("usuarios")
    op.drop_table("tenants")
