"""FTS5 table for conversation message search (T-436).

fts_messages — FTS5 virtual table для полнотекстового поиска по истории
диалогов. Dual-write в коде сервисного слоя (save_messages, delete_conversation,
retention_cleanup), не триггеры БД.
"""

"""revision: 0024
revises: 0023
create_date: 2026-08-23
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создание fts_messages — FTS5 virtual table для поиска по диалогам."""
    # FTS5 встроен в SQLite, не требует extension.
    # content='messages' НЕ используется — dual-write в коде, не external content.
    # conversation_id, message_id — UNINDEXED (для фильтрации, не для поиска).
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_messages "
        "USING fts5(content, conversation_id UNINDEXED, message_id UNINDEXED, role UNINDEXED)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fts_messages")
