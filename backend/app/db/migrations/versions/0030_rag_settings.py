"""rag_settings: настройки RAG-поиска уровня рабочей области (Т-506).

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-29

Одна строка на рабочую область (уникальность по workspace_id).

``relevance_threshold`` — проценты 0–100; 0 = сентинел «фильтр выключен»
(шаг фильтрации по порогу не выполняется вообще). Применяется к скорам
реранкера и только когда реранкер реально отработал.

``max_fragments`` — ограничение сверху 1–8: срез списка после реранкера
до сборки контекста; контракт реранкера «50→8» и токен-лимит не меняются.

Значения по умолчанию (8 и 0) сохраняют поведение существующих
установок: до явного изменения настроек поиск работает как раньше.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column(
            "relevance_threshold",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "max_fragments",
            sa.Integer(),
            nullable=False,
            server_default="8",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", name="uq_rag_settings_workspace"),
    )
    op.create_index("ix_rag_settings_workspace_id", "rag_settings", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_rag_settings_workspace_id", table_name="rag_settings")
    op.drop_table("rag_settings")
