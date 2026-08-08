"""password_hash NOT NULL.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(255),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(255),
            nullable=True,
        )
