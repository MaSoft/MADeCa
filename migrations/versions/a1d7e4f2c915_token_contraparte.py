"""token_contraparte en expediciones (Apartado Segundo.4)

Las expediciones ya existentes reciben un token aleatorio de 64 hex
(dos gen_random_uuid(), nativo desde PostgreSQL 13).

Revision ID: a1d7e4f2c915
Revises: 8f3b2c6a1d40
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1d7e4f2c915"
down_revision: str | None = "8f3b2c6a1d40"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("expediciones", sa.Column("token_contraparte", sa.String(64), nullable=True))
    op.execute(
        "UPDATE expediciones SET token_contraparte = "
        "replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '') "
        "WHERE token_contraparte IS NULL"
    )
    op.create_unique_constraint("uq_expediciones_token_contraparte", "expediciones", ["token_contraparte"])


def downgrade() -> None:
    op.drop_constraint("uq_expediciones_token_contraparte", "expediciones", type_="unique")
    op.drop_column("expediciones", "token_contraparte")
