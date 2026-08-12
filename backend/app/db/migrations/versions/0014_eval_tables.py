"""Eval tables: eval_set, eval_item, eval_run.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-12

eval_run.index_version_id — nullable + ON DELETE SET NULL (как T-117/T-118
для trace/usage_event). cleanup_retired_versions (T-215) удаляет index_version
для retired-версий; каскадное удаление eval_run уничтожило бы историю прогонов.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_set",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column(
            "corpus_id",
            sa.String(36),
            sa.ForeignKey("corpus.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_eval_set_workspace_name"),
    )
    op.create_index("ix_eval_set_corpus_id", "eval_set", ["corpus_id"])

    op.create_table(
        "eval_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column(
            "eval_set_id",
            sa.String(36),
            sa.ForeignKey("eval_set.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.String, nullable=False),
        sa.Column("expected_doc_ids", sa.JSON, nullable=False),
        sa.Column("expected_answer", sa.String, nullable=True),
    )
    op.create_index("ix_eval_item_eval_set_id", "eval_item", ["eval_set_id"])

    op.create_table(
        "eval_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column(
            "eval_set_id",
            sa.String(36),
            sa.ForeignKey("eval_set.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "index_version_id",
            sa.String(36),
            sa.ForeignKey("index_version.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pipeline", sa.JSON, nullable=False),
        sa.Column("metrics", sa.JSON, nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_eval_run_eval_set_id", "eval_run", ["eval_set_id"])
    op.create_index("ix_eval_run_index_version_id", "eval_run", ["index_version_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_run_index_version_id", table_name="eval_run")
    op.drop_index("ix_eval_run_eval_set_id", table_name="eval_run")
    op.drop_table("eval_run")

    op.drop_index("ix_eval_item_eval_set_id", table_name="eval_item")
    op.drop_table("eval_item")

    op.drop_index("ix_eval_set_corpus_id", table_name="eval_set")
    op.drop_table("eval_set")
