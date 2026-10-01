"""add custom thresholds to subscribers

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Use batch_alter_table to support SQLite as well as PostgreSQL
    # Check if columns already exist to make the migration idempotent (fixes partial SQLite failures)
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [col["name"] for col in insp.get_columns("subscribers")]

    with op.batch_alter_table("subscribers") as batch_op:
        if "custom_stoch_low" not in columns:
            batch_op.add_column(sa.Column("custom_stoch_low", sa.Float(), nullable=True))
        if "custom_stoch_high" not in columns:
            batch_op.add_column(sa.Column("custom_stoch_high", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("subscribers") as batch_op:
        batch_op.drop_column("custom_stoch_high")
        batch_op.drop_column("custom_stoch_low")
