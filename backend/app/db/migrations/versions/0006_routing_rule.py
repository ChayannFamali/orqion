"""Routing rule table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "routing_rule",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("order", sa.Integer, nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("is_terminal", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("when_corpus_class", sa.JSON, nullable=True),
        sa.Column("when_role", sa.String(255), nullable=True),
        sa.Column("when_task", sa.String(100), nullable=True),
        sa.Column("when_model_alias", sa.String(255), nullable=True),
        sa.Column("to_models", sa.JSON, nullable=True),
        sa.Column("allow_locality", sa.JSON, nullable=True),
        sa.Column("fallback_models", sa.JSON, nullable=True),
        sa.Column("reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_routing_rule_workspace_id", "routing_rule", ["workspace_id"])
    op.create_index(
        "ix_routing_rule_workspace_order",
        "routing_rule",
        ["workspace_id", "order"],
    )


def downgrade() -> None:
    op.drop_index("ix_routing_rule_workspace_order", table_name="routing_rule")
    op.drop_index("ix_routing_rule_workspace_id", table_name="routing_rule")
    op.drop_table("routing_rule")
