"""superadmins

Revision ID: 184ef1f58c4b
Revises: 47fab727da88
Create Date: 2026-08-08

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "184ef1f58c4b"
down_revision: str | None = "47fab727da88"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "superadmins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("fecha_alta", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("superadmins")
