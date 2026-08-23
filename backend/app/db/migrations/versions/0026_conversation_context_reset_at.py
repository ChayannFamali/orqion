"""Conversation.context_reset_at column (T-442).

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-23

T-442: мягкий сброс контекста диалога. Nullable-маркер фиксирует момент
сброса: сообщения до отметки не входят в историю, отправляемую модели,
при этом видимая лента диалога сохраняется.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("conversation") as batch_op:
            batch_op.add_column(
                sa.Column("context_reset_at", sa.DateTime(timezone=True), nullable=True),
            )
    else:
        op.add_column(
            "conversation",
            sa.Column("context_reset_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("conversation") as batch_op:
            batch_op.drop_column("context_reset_at")
    else:
        op.drop_column("conversation", "context_reset_at")
