"""Usage daily rollup table.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_daily",
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user.id"),
            nullable=True,
        ),
        sa.Column(
            "model_id",
            sa.String(36),
            sa.ForeignKey("model.id"),
            nullable=True,
        ),
        sa.Column("requests", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("errors", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_latency_ms", sa.Integer, nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "date", "user_id", "model_id"),
    )
    op.create_index("ix_usage_daily_workspace_id", "usage_daily", ["workspace_id"])
    op.create_index("ix_usage_daily_date", "usage_daily", ["date"])


def downgrade() -> None:
    op.drop_index("ix_usage_daily_date", table_name="usage_daily")
    op.drop_index("ix_usage_daily_workspace_id", table_name="usage_daily")
    op.drop_table("usage_daily")
