"""Unique constraint on corpus(workspace_id, name).

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # BUG-005: batch_alter_table пересоздаёт таблицу на PostgreSQL (drop PK),
    # что падает из-за зависимых FK от document/index_version/eval_set.
    # dialect guard: прямой ALTER на PostgreSQL.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("corpus", schema=None) as batch_op:
            batch_op.create_unique_constraint("uq_corpus_workspace_name", ["workspace_id", "name"])
    else:
        op.execute(
            "ALTER TABLE corpus ADD CONSTRAINT uq_corpus_workspace_name UNIQUE (workspace_id, name)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("corpus", schema=None) as batch_op:
            batch_op.drop_constraint("uq_corpus_workspace_name", type_="unique")
    else:
        op.execute("ALTER TABLE corpus DROP CONSTRAINT uq_corpus_workspace_name")
