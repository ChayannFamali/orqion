"""Usage event table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
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
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversation.id"),
            nullable=True,
        ),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("message.id"),
            nullable=True,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("cost", sa.Float, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_event_workspace_id", "usage_event", ["workspace_id"])
    op.create_index("ix_usage_event_user_id", "usage_event", ["user_id"])
    op.create_index("ix_usage_event_ts", "usage_event", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_usage_event_ts", table_name="usage_event")
    op.drop_index("ix_usage_event_user_id", table_name="usage_event")
    op.drop_index("ix_usage_event_workspace_id", table_name="usage_event")
    op.drop_table("usage_event")
