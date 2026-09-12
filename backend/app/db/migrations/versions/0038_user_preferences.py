"""Обобщённое хранилище личных настроек пользователя.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-12

Описание ключей живёт в коде (``app/preferences/registry.py``), в таблице
хранится только факт записи значения — новая личная настройка миграции не
требует. Образец — ``workspace_settings`` (0037), разница в области
действия: строка принадлежит пользователю, а не рабочей области.

PK составной ``(workspace_id, user_id, key)``: отдельный ``id`` не нужен,
тройка и есть личность строки, она же обеспечивает единственность записи
на ключ у владельца. ``workspace_id`` — по ADR-3 (каждая таблица несёт
колонку рабочей области); порядок колонок в PK заодно даёт индекс по
префиксу ``(workspace_id, user_id)`` — выборка всех настроек пользователя,
поэтому отдельный индекс не создаётся.

``value`` — JSON с вариантом JSONB на PostgreSQL: в SQLite типа JSONB нет,
а значение читается и сравнивается как документ, а не как непрозрачный
текст.

Колонки ``updated_by`` нет: строку пишет только её владелец, актор всегда
равен ``user_id``, дублировать его отдельной колонкой нечего. FK на
пользователя без ``ondelete`` — как у ``prompt_template.user_id``:
учётные записи в системе не удаляются, а молчаливое каскадное удаление
настроек при будущем удалении учётки было бы неочевидным.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
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
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column(
            "value",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", "key", name="pk_user_preferences"),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
