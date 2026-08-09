"""Conversation and message tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation",
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
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("archived", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_workspace_id", "conversation", ["workspace_id"])
    op.create_index("ix_conversation_user_id", "conversation", ["user_id"])

    op.create_table(
        "message",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversation.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "model_id",
            sa.String(36),
            sa.ForeignKey("model.id"),
            nullable=True,
        ),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("meta", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_message_workspace_id", "message", ["workspace_id"])
    op.create_index("ix_message_conversation_id", "message", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_message_conversation_id", table_name="message")
    op.drop_index("ix_message_workspace_id", table_name="message")
    op.drop_table("message")
    op.drop_index("ix_conversation_user_id", table_name="conversation")
    op.drop_index("ix_conversation_workspace_id", table_name="conversation")
    op.drop_table("conversation")
