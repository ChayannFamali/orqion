"""Corpus and document tables.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "corpus",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("data_class", sa.String(10), nullable=True),
        # FK на index_version добавляется в T-205 (таблицы ещё нет)
        sa.Column("active_index_version_id", sa.String(36), nullable=True),
        sa.Column(
            "pinned_model_id",
            sa.String(36),
            sa.ForeignKey("model.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # workspace_id индекс создаётся автоматически через index=True в WorkspaceMixin

    op.create_table(
        "document",
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
        sa.Column("blob_uri", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column(
            "mime",
            sa.String(255),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column(
            "source_type",
            sa.String(50),
            nullable=False,
            server_default="upload",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "sha256",
            name="uq_document_workspace_sha256",
        ),
    )
    # workspace_id индекс создаётся автоматически через index=True в WorkspaceMixin
    op.create_index("ix_document_corpus_id", "document", ["corpus_id"])


def downgrade() -> None:
    op.drop_index("ix_document_corpus_id", table_name="document")
    op.drop_table("document")
    op.drop_table("corpus")
