"""sesiones_validas_desde en usuarios

Permite anular las sesiones abiertas de un usuario (p. ej. al restablecer
su contraseña).

Revision ID: 5c1e9a7d3b20
Revises: 184ef1f58c4b
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e9a7d3b20"
down_revision: str | None = "184ef1f58c4b"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("sesiones_validas_desde", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("usuarios", "sesiones_validas_desde")
