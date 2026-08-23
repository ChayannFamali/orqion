"""Полнотекстовый поиск по истории диалогов (T-436).

FTS5-поиск по fts_messages с фильтрацией по user_id в WHERE (до MATCH,
не пост-фильтрация — §8.2 «права проверляются до поиска»). Экранирование
запроса — app.utils.fts5.escape_fts5_query (прецедент T-212/BUG-003).

Dual-write: fts_messages обновляется в save_messages (insert),
delete_conversation и retention_cleanup (delete) — см. комментарии в
соответствующих модулях. Edit/regenerate (T-305) не удаляет Message на
бэкенде (фронтенд обрезает localMessages в state, отправляет новый запрос
поверх; старые Message остаются в БД) — синхронизация FTS5 не нужна.
Если T-305 когда-либо добавит DELETE Message на бэкенде, обязан добавить
симметричный DELETE FROM fts_messages.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.fts5 import escape_fts5_query


@dataclass
class MessageSearchHit:
    """Результат поиска по истории диалогов."""

    message_id: str
    conversation_id: str
    role: str
    content: str
    score: float


async def search_messages(
    session: AsyncSession,
    query: str,
    user_id: str,
    workspace_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[MessageSearchHit]:
    """Полнотекстовый поиск по диалогам пользователя.

    FTS5 MATCH + JOIN с conversation для фильтрации по user_id в WHERE
    (не пост-фильтрация). bm25(fts_messages) — меньше = релевантнее
    (стандарт FTS5), поэтому ORDER BY score ASC.
    """
    fts_query = escape_fts5_query(query)
    if not fts_query:
        return []

    stmt = text(
        """
        SELECT
            fts.message_id AS message_id,
            fts.conversation_id AS conversation_id,
            fts.role AS role,
            fts.content AS content,
            bm25(fts_messages) AS score
        FROM fts_messages AS fts
        JOIN conversation c ON c.id = fts.conversation_id
        WHERE fts_messages MATCH :query
          AND c.user_id = :user_id
          AND c.workspace_id = :workspace_id
          AND c.archived = 0
        ORDER BY score ASC
        LIMIT :limit OFFSET :offset
        """
    )
    result = await session.execute(
        stmt,
        {
            "query": fts_query,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "limit": limit,
            "offset": offset,
        },
    )
    rows = result.all()
    return [
        MessageSearchHit(
            message_id=row.message_id,
            conversation_id=row.conversation_id,
            role=row.role,
            content=row.content,
            score=float(row.score),
        )
        for row in rows
    ]
