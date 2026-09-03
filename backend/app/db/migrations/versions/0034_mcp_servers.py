"""Т-503: реестр серверов протокола передачи контекста моделям.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-03

Админский реестр серверов протокола (решения 1–3 дизайн-ревью Т-503 /
пункт 8 ADR-21): транспорт только HTTP к явному адресу, локальные
процессы не запускаются; секреты — тот же механизм шифрования, что у
ключей провайдеров (``api_key_enc``, AES-GCM), в ответах не
возвращаются.

Имя сервера уникально в рабочей области и служит неймспейсом его
инструментов в едином реестре (``<имя_сервера>.<имя_инструмента>``);
валидация формата имени на уровне API не допускает точку, поэтому
коллизия с встроенными инструментами (без префикса) исключена
построением.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_server",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("api_key_enc", sa.String(1024), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_mcp_server_workspace_name",
        ),
    )
    op.create_index("ix_mcp_server_workspace_id", "mcp_server", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_server_workspace_id", table_name="mcp_server")
    op.drop_table("mcp_server")
