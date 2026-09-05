"""Т-508: скиллы — пакеты конфигурации агентного прогона.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-05

Декларативный скилл (решение 1 мини-дизайн-ревью): фрагмент системного
промпта, подмножество инструментов единого реестра и дефолт
``max_tokens``. Исполняемого содержимого нет — санкционированный путь
исполнения уже существует (реестр серверов протокола, миграция 0034).

``tools`` хранится JSON-списком строк (прецедент ``routing_rule``).
Пустой список означает НОЛЬ инструментов, а не «не сужать» (решение 6,
правка пользователя): расширение доступа остаётся явным перечислением
имён в момент правки скилла.

Имя уникально в рабочей области (паттерн ``mcp_server.name``). Колонки
``user_id`` нет — владение админское (решение 4); путь к личным скиллам
позже повторяет схему Т-507 (nullable ``user_id`` + правило
редактирования) и переделки таблицы не потребует.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_skill",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(512), nullable=False, server_default=""),
        sa.Column("prompt_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("tools", sa.JSON, nullable=True),
        sa.Column("default_max_tokens", sa.Integer, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_agent_skill_workspace_name",
        ),
    )
    op.create_index("ix_agent_skill_workspace_id", "agent_skill", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_skill_workspace_id", table_name="agent_skill")
    op.drop_table("agent_skill")
