"""Обобщённое хранилище служебных настроек рабочей области.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-10

Описание ключей живёт в коде (``app/settings/registry.py``), в таблице
хранится только факт записи значения — новая настройка миграции не
требует.

PK составной ``(workspace_id, key)``: отдельный ``id`` не нужен, пара и
есть личность строки, она же обеспечивает единственность записи на ключ.
``key`` — строка до 128 знаков, поэтому в ``audit_log.object_id``
(String(36)) ключ не пишется: там ``workspace_id``, а ключ в ``meta``.

``value`` — JSON с вариантом JSONB на PostgreSQL: в SQLite типа JSONB нет,
а значение читается и сравнивается как документ, а не как непрозрачный
текст.

``updated_by`` — nullable с ``ondelete="SET NULL"``: удаление учётной
записи не блокируется историей правок.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_settings",
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column(
            "value",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_by",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("workspace_id", "key", name="pk_workspace_settings"),
    )
    op.create_index(
        "ix_workspace_settings_workspace_id",
        "workspace_settings",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_settings_workspace_id", table_name="workspace_settings")
    op.drop_table("workspace_settings")
