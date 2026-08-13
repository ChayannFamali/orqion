"""Add error column to document for parse failure reasons.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.add_column(sa.Column("error", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.drop_column("error")
