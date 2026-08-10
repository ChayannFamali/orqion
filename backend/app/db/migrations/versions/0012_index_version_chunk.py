"""Index version and chunk tables, FK on corpus.active_index_version_id.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "index_version",
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
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("chunker", sa.String(50), nullable=False),
        sa.Column("chunker_version", sa.String(20), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="building",
        ),
        sa.Column("stats", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # workspace_id индекс создаётся автоматически через index=True в WorkspaceMixin
    op.create_index("ix_index_version_corpus_id", "index_version", ["corpus_id"])

    op.create_table(
        "chunk",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column(
            "index_version_id",
            sa.String(36),
            sa.ForeignKey("index_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("text", sa.String, nullable=False),
        sa.Column("meta", sa.JSON, nullable=True),
    )
    op.create_index("ix_chunk_index_version_id", "chunk", ["index_version_id"])
    op.create_index("ix_chunk_document_id", "chunk", ["document_id"])

    # FK на corpus.active_index_version_id → index_version.id
    # SQLite не умеет ALTER ADD CONSTRAINT — используем batch_alter_table
    with op.batch_alter_table("corpus") as batch_op:
        batch_op.alter_column(
            "active_index_version_id",
            existing_type=sa.String(36),
            nullable=True,
        )
        batch_op.create_foreign_key(
            "fk_corpus_active_index_version_id",
            "index_version",
            ["active_index_version_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("corpus") as batch_op:
        batch_op.drop_constraint("fk_corpus_active_index_version_id", type_="foreignkey")

    op.drop_index("ix_chunk_document_id", table_name="chunk")
    op.drop_index("ix_chunk_index_version_id", table_name="chunk")
    op.drop_index("ix_index_version_corpus_id", table_name="index_version")
    op.drop_table("chunk")
    op.drop_table("index_version")
