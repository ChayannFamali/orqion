"""Т-509: профили агентов; диалог помнит профиль и запрос остановки.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-09

Три изменения одной миграцией — все три нужны одновременно и по
отдельности неработоспособны:

- таблица ``agent_profile`` — переиспользуемая конфигурация агентного
  диалога (решение 1): модель + скилл. Отдельного поля системного
  промпта и отдельного списка инструментов НЕТ — эту роль полностью
  выполняет ``agent_skill`` (миграция 0035), вторая сущность
  «промпт + инструменты» не заводится;
- ``conversation.agent_profile_id`` — диалог, созданный от профиля,
  фиксирует его (решение 2, аналог ``corpus.pinned_model_id``).
  Nullable: ad-hoc агентный диалог и обычный чат профиля не имеют
  (решение 9 — ad-hoc путь сохраняется);
- ``conversation.stop_requested`` — флаг остановки прогона (решение 7).
  Поле общее для ЛЮБОГО агентного диалога, не только профильного:
  останавливать имеет смысл длинный прогон, а не конкретную
  конфигурацию.

``created_by`` — nullable c ``ondelete="SET NULL"`` по паттерну
``user.team_id``: удаление учётной записи не блокируется профилями.
Удаление самого профиля при наличии диалогов запрещается на уровне API
(409, решение 8), каскада нет — как у провайдера с моделями.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_profile",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(512), nullable=False, server_default=""),
        sa.Column(
            "model_id",
            sa.String(36),
            sa.ForeignKey("model.id"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            sa.String(36),
            sa.ForeignKey("agent_skill.id"),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_agent_profile_workspace_name",
        ),
    )
    op.create_index("ix_agent_profile_workspace_id", "agent_profile", ["workspace_id"])

    # FK-колонка на существующей таблице (решение 4). SQLite не умеет
    # ALTER ADD CONSTRAINT, поэтому колонка с внешним ключом добавляется
    # через batch_alter_table; на PostgreSQL — прямым ALTER, потому что
    # batch пересоздаёт таблицу и роняет PK, а от ``conversation`` зависит
    # FK ``message.conversation_id`` (прецедент BUG-005, паттерн 0022
    # ``user.team_id``).
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("conversation") as batch_op:
            batch_op.add_column(
                sa.Column("agent_profile_id", sa.String(36), nullable=True),
            )
            batch_op.create_foreign_key(
                "fk_conversation_agent_profile_id",
                "agent_profile",
                ["agent_profile_id"],
                ["id"],
            )
            batch_op.create_index("ix_conversation_agent_profile_id", ["agent_profile_id"])
    else:
        op.add_column(
            "conversation",
            sa.Column("agent_profile_id", sa.String(36), nullable=True),
        )
        op.create_foreign_key(
            "fk_conversation_agent_profile_id",
            "conversation",
            "agent_profile",
            ["agent_profile_id"],
            ["id"],
        )
        op.create_index("ix_conversation_agent_profile_id", "conversation", ["agent_profile_id"])

    # Булев флаг без внешнего ключа — обычный add_column на обеих СУБД
    # (паттерн ``conversation.mode`` в 0033).
    op.add_column(
        "conversation",
        sa.Column(
            "stop_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("conversation", "stop_requested")

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("conversation") as batch_op:
            batch_op.drop_index("ix_conversation_agent_profile_id")
            batch_op.drop_constraint("fk_conversation_agent_profile_id", type_="foreignkey")
            batch_op.drop_column("agent_profile_id")
    else:
        op.drop_index("ix_conversation_agent_profile_id", table_name="conversation")
        op.drop_constraint("fk_conversation_agent_profile_id", "conversation", type_="foreignkey")
        op.drop_column("conversation", "agent_profile_id")

    op.drop_index("ix_agent_profile_workspace_id", table_name="agent_profile")
    op.drop_table("agent_profile")
