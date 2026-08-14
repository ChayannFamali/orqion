"""Vector store: FTS5 table + chunk_id mapping (T-212).

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-11

fts_chunks — FTS5 virtual table для разреженного поиска (BM25).
Фильтрация по index_version_id.

vec_chunk_map — маппинг rowid (vec0/FTS5) ↔ chunk_id (UUID String(36)).

vec_chunks (sqlite-vec) создаётся в runtime SQLiteVectorStore._get_conn,
т.к. требует загруженного extension — SQLAlchemy async engine не позволяет
load_extension через SQL.

BUG-005: dialect guard — FTS5 и vec_chunk_map создаются только для SQLite.
На PostgreSQL векторный store использует отдельный SQLite-файл (split stack,
Вариант A T-410) или Qdrant — эти таблицы не нужны в основной БД.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создание FTS5 таблицы и vec_chunk_map.

    FTS5 встроен в SQLite, не требует extension.
    vec0 таблица (sqlite-vec) создаётся в runtime SQLiteVectorStore.

    BUG-005: dialect guard — на PostgreSQL эти таблицы не создаются.
    Векторный store использует отдельный SQLite-файл (split stack)
    или Qdrant — FTS5/vec0 живут только в SQLite.
    """
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks "
            "USING fts5(text, index_version_id UNINDEXED)"
        )
    )
    conn.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS vec_chunk_map "
            "(rowid INTEGER PRIMARY KEY, chunk_id TEXT NOT NULL, "
            "index_version_id TEXT NOT NULL)"
        )
    )


def downgrade() -> None:
    """Удаление FTS5, vec_chunk_map и vec0 таблиц.

    BUG-005: dialect guard — на PostgreSQL таблиц не было, нечего удалять.
    """
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return

    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS vec_chunks"))
    conn.execute(sa.text("DROP TABLE IF EXISTS vec_chunk_map"))
    conn.execute(sa.text("DROP TABLE IF EXISTS fts_chunks"))
