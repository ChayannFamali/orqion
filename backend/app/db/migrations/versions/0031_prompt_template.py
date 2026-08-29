"""prompt_template: личные сохранённые промпты пользователей (Т-507).

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-29

Первая версия — только личные шаблоны: ``user_id`` — владелец, CRUD только
у владельца. ``user_id`` допускает пустое значение как путь к общим
шаблонам рабочей области (решение дизайн-ревью Т-507, по образцу Т-506);
в этой версии всегда заполнен.

``title`` — до 200 символов. ``body`` — текст шаблона без плейсхолдеров;
предельная длина проверяется приложением по настройке
``prompt_template_max_chars`` (по умолчанию 8192), число шаблонов на
пользователя — по ``prompt_templates_max_per_user`` (по умолчанию 100).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_template",
        sa.Column("id", sa.String(36), primary_key=True),
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
            nullable=True,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_prompt_template_workspace_id", "prompt_template", ["workspace_id"])
    op.create_index("ix_prompt_template_user_id", "prompt_template", ["user_id"])
    op.create_index("ix_prompt_template_ws_user", "prompt_template", ["workspace_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_prompt_template_ws_user", table_name="prompt_template")
    op.drop_index("ix_prompt_template_user_id", table_name="prompt_template")
    op.drop_index("ix_prompt_template_workspace_id", table_name="prompt_template")
    op.drop_table("prompt_template")
