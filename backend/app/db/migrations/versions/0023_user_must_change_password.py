"""User.must_change_password column (TD-10).

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-16

TD-10: User creation via UI. Generated password must be changed on first login.
New boolean column: must_change_password (default False, NOT NULL).
Admin sets True when creating a user; change-password endpoint sets False.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "must_change_password",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )
    else:
        op.add_column(
            "user",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user") as batch_op:
            batch_op.drop_column("must_change_password")
    else:
        op.drop_column("user", "must_change_password")
