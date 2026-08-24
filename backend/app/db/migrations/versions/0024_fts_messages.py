"""FTS5 table for conversation message search (T-436).

fts_messages — FTS5 virtual table для полнотекстового поиска по истории
диалогов. Dual-write в коде сервисного слоя (save_messages, delete_conversation,
retention_cleanup), не триггеры БД.

BUG-017: dialect guard — fts_messages создаётся только для SQLite
(повторение BUG-005, исправленного в 0013). На PostgreSQL таблицы нет:
dual-write в коде пропускается, поиск отказывает 501.
"""

"""revision: 0024
revises: 0023
create_date: 2026-08-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создание fts_messages — FTS5 virtual table для поиска по диалогам.

    BUG-017: dialect guard — на PostgreSQL виртуальная таблица не создаётся.
    """
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return

    # FTS5 встроен в SQLite, не требует extension.
    # content='messages' НЕ используется — dual-write в коде, не external content.
    # conversation_id, message_id — UNINDEXED (для фильтрации, не для поиска).
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS fts_messages "
            "USING fts5(content, conversation_id UNINDEXED, message_id UNINDEXED, role UNINDEXED)"
        )
    )


def downgrade() -> None:
    """BUG-017: dialect guard — на PostgreSQL таблицы не было, нечего удалять."""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return

    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS fts_messages"))
