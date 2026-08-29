"""rag_settings: число групп графа связей документов (Т-505).

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-30

``cluster_count`` — сколько семантических групп строит граф связей
документов; задаёт администратор в настройках поиска (право
``manage_corpora``), автоподбор и автоназвания не предусмотрены
(решение 4 дизайн-ревью Т-505). Диапазон 2–20 проверяет приложение
(схема запроса).

Колонка NOT NULL с server_default 8 — существующие строки настроек
получают значение по умолчанию, поведение без явного изменения не
меняется. Значение синхронно дефолтам роута настроек.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rag_settings",
        sa.Column(
            "cluster_count",
            sa.Integer(),
            nullable=False,
            server_default="8",
        ),
    )


def downgrade() -> None:
    op.drop_column("rag_settings", "cluster_count")
