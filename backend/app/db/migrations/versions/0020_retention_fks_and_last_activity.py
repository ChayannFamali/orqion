"""Retention FKs + conversation.last_activity_at (T-406).

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-14

T-406: Сроки хранения. Два изменения схемы:
1. ondelete="SET NULL" для trace/usage_event FK к conversation/message
   (T-118 пометка — docstring обещал, но не был объявлен на уровне схемы).
2. conversation.last_activity_at — основание для retention диалогов
   (не created_at, чтобы активные старые диалоги не удалялись).

Backfill выполняется в 3 шага (ADD COLUMN → UPDATE → NOT NULL),
т.к. server_default не может ссылаться на другую колонку.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # 1. FK ondelete="SET NULL" для trace
    # BUG-005: batch_alter_table(recreate="always") на PostgreSQL падает —
    # drop PK trace конфликтует с зависимым FK span_trace_id_fkey.
    # dialect guard: прямой ALTER на PostgreSQL.
    if is_sqlite:
        with op.batch_alter_table("trace") as batch_op:
            batch_op.alter_column(
                "conversation_id",
                existing_type=sa.String(36),
                nullable=True,
                existing_server_default=None,
                postgresql_server_default=None,
            )
            batch_op.alter_column(
                "message_id",
                existing_type=sa.String(36),
                nullable=True,
                existing_server_default=None,
                postgresql_server_default=None,
            )

        with op.batch_alter_table("trace", recreate="always") as batch_op:
            batch_op.alter_column(
                "conversation_id",
                existing_type=sa.String(36),
                nullable=True,
                existing_server_default=None,
                postgresql_server_default=None,
            )
            batch_op.alter_column(
                "message_id",
                existing_type=sa.String(36),
                nullable=True,
                existing_server_default=None,
                postgresql_server_default=None,
            )
            batch_op.create_foreign_key(
                "fk_trace_conversation_id",
                "conversation",
                ["conversation_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_foreign_key(
                "fk_trace_message_id",
                "message",
                ["message_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        # PostgreSQL: drop old FK (auto-named *_fkey or fk_trace_*),
        # alter column, add new FK with ondelete.
        # IF EXISTS: на повторном upgrade после downgrade имя уже fk_trace_*.
        op.execute("ALTER TABLE trace DROP CONSTRAINT IF EXISTS trace_conversation_id_fkey")
        op.execute("ALTER TABLE trace DROP CONSTRAINT IF EXISTS fk_trace_conversation_id")
        op.execute("ALTER TABLE trace DROP CONSTRAINT IF EXISTS trace_message_id_fkey")
        op.execute("ALTER TABLE trace DROP CONSTRAINT IF EXISTS fk_trace_message_id")
        op.execute("ALTER TABLE trace ALTER COLUMN conversation_id DROP NOT NULL")
        op.execute("ALTER TABLE trace ALTER COLUMN message_id DROP NOT NULL")
        op.execute(
            "ALTER TABLE trace ADD CONSTRAINT fk_trace_conversation_id "
            "FOREIGN KEY (conversation_id) REFERENCES conversation(id) "
            "ON DELETE SET NULL"
        )
        op.execute(
            "ALTER TABLE trace ADD CONSTRAINT fk_trace_message_id "
            "FOREIGN KEY (message_id) REFERENCES message(id) "
            "ON DELETE SET NULL"
        )

    # 2. FK ondelete="SET NULL" для usage_event
    if is_sqlite:
        with op.batch_alter_table("usage_event", recreate="always") as batch_op:
            batch_op.alter_column(
                "conversation_id",
                existing_type=sa.String(36),
                nullable=True,
                existing_server_default=None,
                postgresql_server_default=None,
            )
            batch_op.alter_column(
                "message_id",
                existing_type=sa.String(36),
                nullable=True,
                existing_server_default=None,
                postgresql_server_default=None,
            )
            batch_op.create_foreign_key(
                "fk_usage_event_conversation_id",
                "conversation",
                ["conversation_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_foreign_key(
                "fk_usage_event_message_id",
                "message",
                ["message_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.execute(
            "ALTER TABLE usage_event DROP CONSTRAINT IF EXISTS usage_event_conversation_id_fkey"
        )
        op.execute(
            "ALTER TABLE usage_event DROP CONSTRAINT IF EXISTS fk_usage_event_conversation_id"
        )
        op.execute("ALTER TABLE usage_event DROP CONSTRAINT IF EXISTS usage_event_message_id_fkey")
        op.execute("ALTER TABLE usage_event DROP CONSTRAINT IF EXISTS fk_usage_event_message_id")
        op.execute("ALTER TABLE usage_event ALTER COLUMN conversation_id DROP NOT NULL")
        op.execute("ALTER TABLE usage_event ALTER COLUMN message_id DROP NOT NULL")
        op.execute(
            "ALTER TABLE usage_event ADD CONSTRAINT fk_usage_event_conversation_id "
            "FOREIGN KEY (conversation_id) REFERENCES conversation(id) "
            "ON DELETE SET NULL"
        )
        op.execute(
            "ALTER TABLE usage_event ADD CONSTRAINT fk_usage_event_message_id "
            "FOREIGN KEY (message_id) REFERENCES message(id) "
            "ON DELETE SET NULL"
        )

    # 3. conversation.last_activity_at — 3-шаговый backfill
    # Шаг 1: ADD COLUMN (nullable, без default)
    with op.batch_alter_table("conversation") as batch_op:
        batch_op.add_column(
            sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True)
        )

    # Шаг 2: UPDATE — backfill из created_at
    op.execute("UPDATE conversation SET last_activity_at = created_at")

    # Шаг 3: NOT NULL
    with op.batch_alter_table("conversation") as batch_op:
        batch_op.alter_column(
            "last_activity_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # Удаляем last_activity_at
    with op.batch_alter_table("conversation") as batch_op:
        batch_op.drop_column("last_activity_at")

    # Возвращаем FK без ondelete (NO ACTION)
    if is_sqlite:
        with op.batch_alter_table("usage_event", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_usage_event_conversation_id", type_="foreignkey")
            batch_op.drop_constraint("fk_usage_event_message_id", type_="foreignkey")
            batch_op.create_foreign_key(
                "fk_usage_event_conversation_id",
                "conversation",
                ["conversation_id"],
                ["id"],
            )
            batch_op.create_foreign_key(
                "fk_usage_event_message_id",
                "message",
                ["message_id"],
                ["id"],
            )

        with op.batch_alter_table("trace", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_trace_conversation_id", type_="foreignkey")
            batch_op.drop_constraint("fk_trace_message_id", type_="foreignkey")
            batch_op.create_foreign_key(
                "fk_trace_conversation_id",
                "conversation",
                ["conversation_id"],
                ["id"],
            )
            batch_op.create_foreign_key(
                "fk_trace_message_id",
                "message",
                ["message_id"],
                ["id"],
            )
    else:
        op.execute("ALTER TABLE usage_event DROP CONSTRAINT fk_usage_event_conversation_id")
        op.execute("ALTER TABLE usage_event DROP CONSTRAINT fk_usage_event_message_id")
        op.execute(
            "ALTER TABLE usage_event ADD CONSTRAINT fk_usage_event_conversation_id "
            "FOREIGN KEY (conversation_id) REFERENCES conversation(id)"
        )
        op.execute(
            "ALTER TABLE usage_event ADD CONSTRAINT fk_usage_event_message_id "
            "FOREIGN KEY (message_id) REFERENCES message(id)"
        )
        op.execute("ALTER TABLE trace DROP CONSTRAINT fk_trace_conversation_id")
        op.execute("ALTER TABLE trace DROP CONSTRAINT fk_trace_message_id")
        op.execute(
            "ALTER TABLE trace ADD CONSTRAINT fk_trace_conversation_id "
            "FOREIGN KEY (conversation_id) REFERENCES conversation(id)"
        )
        op.execute(
            "ALTER TABLE trace ADD CONSTRAINT fk_trace_message_id "
            "FOREIGN KEY (message_id) REFERENCES message(id)"
        )
