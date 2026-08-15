"""usage_daily: sentinel UUID for nullable user_id/model_id in PK (BUG-008).

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-15

BUG-008: PrimaryKeyConstraint("workspace_id", "date", "user_id", "model_id")
with nullable=True on user_id/model_id. PostgreSQL PK → implicit NOT NULL.
aggregate_day groups by nullable fields → NULL → NotNullViolationError on
PostgreSQL (SQLite non-enforcement masked the bug).

Fix: replace NULL with sentinel UUID "00000000-0000-0000-0000-000000000000"
(RFC 4122 §4.17 Nil UUID) in aggregate_day. Drop FK constraints on user_id/
model_id — sentinel UUIDs don't exist in user/model tables.

Migration steps:
1. Drop FK constraints on user_id, model_id (PostgreSQL: both auto-named
   usage_daily_{column}_fkey; SQLite: batch_alter_table).
2. Backfill: UPDATE usage_daily SET user_id/model_id = sentinel WHERE IS NULL.
   No-op on PostgreSQL (NULL rows were never inserted), needed on SQLite.
3. Set NOT NULL on user_id, model_id (PostgreSQL: already NOT NULL from PK,
   but ALTER COLUMN ... SET NOT NULL is idempotent; SQLite: batch_alter_table).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NIL_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "sqlite":
        # SQLite: batch_alter_table recreates the table.
        # FK on user_id/model_id was created without explicit name in 0010,
        # so we can't drop by name. SQLite doesn't enforce FKs by default,
        # so leaving them is harmless. Just set NOT NULL.
        with op.batch_alter_table("usage_daily", recreate="always") as batch_op:
            batch_op.alter_column(
                "user_id",
                existing_type=sa.String(36),
                nullable=False,
            )
            batch_op.alter_column(
                "model_id",
                existing_type=sa.String(36),
                nullable=False,
            )
    else:
        # PostgreSQL: FK names confirmed on live container
        # (psql \d usage_daily):
        #   usage_daily_user_id_fkey
        #   usage_daily_model_id_fkey
        # Columns are already NOT NULL from PK constraint — no ALTER needed.
        op.execute("ALTER TABLE usage_daily DROP CONSTRAINT IF EXISTS usage_daily_user_id_fkey")
        op.execute("ALTER TABLE usage_daily DROP CONSTRAINT IF EXISTS usage_daily_model_id_fkey")

    # Backfill: NULL → sentinel (no-op on PostgreSQL, needed on SQLite)
    op.execute(f"UPDATE usage_daily SET user_id = '{NIL_ID}' WHERE user_id IS NULL")
    op.execute(f"UPDATE usage_daily SET model_id = '{NIL_ID}' WHERE model_id IS NULL")


def downgrade() -> None:
    bind = op.get_bind()

    # Restore NULLs for sentinel rows (best-effort)
    op.execute(f"UPDATE usage_daily SET user_id = NULL WHERE user_id = '{NIL_ID}'")
    op.execute(f"UPDATE usage_daily SET model_id = NULL WHERE model_id = '{NIL_ID}'")

    if bind.dialect.name == "sqlite":
        # SQLite: PK keeps columns NOT NULL, can't ALTER to nullable while in PK.
        # FKs were never explicitly named and can't be re-created by name.
        # SQLite doesn't enforce FKs by default — no-op.
        pass
    else:
        # PK constraint keeps columns NOT NULL on PostgreSQL — DROP NOT NULL
        # is not allowed while the column is in a PK. Just restore FK constraints.
        op.execute(
            "ALTER TABLE usage_daily ADD CONSTRAINT usage_daily_user_id_fkey "
            'FOREIGN KEY (user_id) REFERENCES "user"(id)'
        )
        op.execute(
            "ALTER TABLE usage_daily ADD CONSTRAINT usage_daily_model_id_fkey "
            "FOREIGN KEY (model_id) REFERENCES model(id)"
        )
