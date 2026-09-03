"""Т-502: модель умеет инструменты; разговоры различают режим.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-03

Две колонки агентного модуля (решения 3 и 10 пересмотренного
дизайн-ревью Т-502):

- ``model.supports_tools`` — ручной флаг пригодности модели к
  инструментам по паттерну ``reasoning_toggleable`` (0029, каркас
  Т-445) и ``supports_reasoning`` (Т-113): администратор отмечает сам,
  автоопределение пробой не делается. Точка создания агентного
  диалога видна только при наличии хотя бы одной модели с флагом.
- ``conversation.mode`` — режим разговора: ``chat`` (обычный) или
  ``agent`` (агентный диалог). Точка входа в агентный модуль —
  отдельная карточка, обычный чат поведение не меняет.

Обе колонки NOT NULL с server_default — существующие строки получают
значение по умолчанию, поведение прежних запросов не меняется.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model",
        sa.Column(
            "supports_tools",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "conversation",
        sa.Column(
            "mode",
            sa.String(length=10),
            nullable=False,
            server_default="chat",
        ),
    )


def downgrade() -> None:
    op.drop_column("conversation", "mode")
    op.drop_column("model", "supports_tools")
