"""Trace and span tables.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace",
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
        sa.Column("total_ms", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trace_workspace_id", "trace", ["workspace_id"])
    op.create_index("ix_trace_user_id", "trace", ["user_id"])
    op.create_index("ix_trace_ts", "trace", ["ts"])

    op.create_table(
        "span",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column(
            "trace_id",
            sa.String(36),
            sa.ForeignKey("trace.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(36),
            sa.ForeignKey("span.id"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_span_workspace_id", "span", ["workspace_id"])
    op.create_index("ix_span_trace_id", "span", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_span_trace_id", table_name="span")
    op.drop_index("ix_span_workspace_id", table_name="span")
    op.drop_table("span")
    op.drop_index("ix_trace_ts", table_name="trace")
    op.drop_index("ix_trace_user_id", table_name="trace")
    op.drop_index("ix_trace_workspace_id", table_name="trace")
    op.drop_table("trace")
