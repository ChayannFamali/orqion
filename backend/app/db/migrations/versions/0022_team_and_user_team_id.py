"""Team table + User.team_id for manager-scoped analytics (T-402a).

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-16

T-402a: Manager sees analytics only for users in their team.
- New table: team (workspace_id, id, name, timestamps).
- New column: user.team_id (nullable FK → team.id, ondelete=SET NULL).
- NULL team_id = not in any team. Manager with NULL team_id sees empty analytics.
- Admin (capabilities=["*"]) bypasses team filter — sees entire workspace.

ondelete="SET NULL" is set explicitly now, even though Team CRUD doesn't
exist yet — prevents future IntegrityError when team deletion is implemented.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create team table
    op.create_table(
        "team",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspace.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_team_workspace_name"),
    )
    op.create_index("ix_team_workspace_id", "team", ["workspace_id"])

    # Add team_id column to user table
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(
                sa.Column("team_id", sa.String(36), nullable=True),
            )
            batch_op.create_foreign_key(
                "fk_user_team_id",
                "team",
                ["team_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index("ix_user_team_id", ["team_id"])
    else:
        op.add_column("user", sa.Column("team_id", sa.String(36), nullable=True))
        op.create_foreign_key(
            "fk_user_team_id",
            "user",
            "team",
            ["team_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_user_team_id", "user", ["team_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user") as batch_op:
            batch_op.drop_index("ix_user_team_id")
            batch_op.drop_constraint("fk_user_team_id", type_="foreignkey")
            batch_op.drop_column("team_id")
    else:
        op.drop_index("ix_user_team_id", table_name="user")
        op.drop_constraint("fk_user_team_id", "user", type_="foreignkey")
        op.drop_column("user", "team_id")

    op.drop_index("ix_team_workspace_id", table_name="team")
    op.drop_table("team")
