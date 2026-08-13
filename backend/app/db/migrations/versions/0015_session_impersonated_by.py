"""Add impersonated_by to session table for impersonation tracking.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session",
        sa.Column("impersonated_by", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session", "impersonated_by")
