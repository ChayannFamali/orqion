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
    with op.batch_alter_table("corpus", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_corpus_workspace_name", ["workspace_id", "name"])


def downgrade() -> None:
    with op.batch_alter_table("corpus", schema=None) as batch_op:
        batch_op.drop_constraint("uq_corpus_workspace_name", type_="unique")
