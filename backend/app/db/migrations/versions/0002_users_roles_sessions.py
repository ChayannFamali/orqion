"""Users, roles, sessions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "role",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("policy", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_role_workspace_id", "role", ["workspace_id"])

    op.create_table(
        "user",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column(
            "role_id",
            sa.String(36),
            sa.ForeignKey("role.id"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "email",
            name="uq_user_workspace_email",
        ),
    )
    op.create_index("ix_user_workspace_id", "user", ["workspace_id"])

    op.create_table(
        "session",
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_session_workspace_id", "session", ["workspace_id"])
    op.create_index("ix_session_user_id", "session", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_session_user_id", table_name="session")
    op.drop_index("ix_session_workspace_id", table_name="session")
    op.drop_table("session")
    op.drop_index("ix_user_workspace_id", table_name="user")
    op.drop_table("user")
    op.drop_index("ix_role_workspace_id", table_name="role")
    op.drop_table("role")
