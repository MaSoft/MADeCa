"""posición de cada palabra del documento original del borrador

Revision ID: e6c4a9d2b157
Revises: d3a8b5c2e710
Create Date: 2026-10-11

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6c4a9d2b157"
down_revision: str | None = "d3a8b5c2e710"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("borradores_deca", sa.Column("posiciones", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("borradores_deca", "posiciones")
