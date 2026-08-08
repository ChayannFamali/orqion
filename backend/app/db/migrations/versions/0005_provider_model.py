"""Provider and model tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=False),
        sa.Column("api_key_enc", sa.String(1024), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("capabilities", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_provider_workspace_id", "provider", ["workspace_id"])

    op.create_table(
        "model",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            sa.String(36),
            sa.ForeignKey("provider.id"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("upstream_name", sa.String(255), nullable=False),
        sa.Column("locality", sa.String(20), nullable=False),
        sa.Column("max_input_tokens", sa.Integer, nullable=True),
        sa.Column("max_output_tokens", sa.Integer, nullable=True),
        sa.Column("supports_reasoning", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("cost_in", sa.Float, nullable=True),
        sa.Column("cost_out", sa.Float, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "alias",
            name="uq_model_workspace_alias",
        ),
    )
    op.create_index("ix_model_workspace_id", "model", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_model_workspace_id", table_name="model")
    op.drop_table("model")
    op.drop_index("ix_provider_workspace_id", table_name="provider")
    op.drop_table("provider")
