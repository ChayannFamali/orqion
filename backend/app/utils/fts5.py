"""Общие утилиты для FTS5 (T-212/BUG-003, T-436).

Экранирование пользовательского запроса для FTS5 MATCH — используется
и в RAG vector_store (T-212), и в полнотекстовом поиске по диалогам (T-436).
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession


def fts5_available(session: AsyncSession) -> bool:
    """Доступен ли FTS5-поиск по диалогам для этой БД.

    FTS5-таблица fts_messages создаётся только для SQLite (диалект-гейт
    миграции 0024, прецедент BUG-005/0013). На PostgreSQL таблицы нет —
    dual-write пропускается, поиск отказывает явно (BUG-017).
    """
    bind = session.bind
    if bind is None:
        return False
    return bind.dialect.name == "sqlite"


# FTS5 спецсимволы: " * - : ( ) ? ^ ! & |
_FTS5_SPECIAL = re.compile(r'["*\-:()?!&|]')


def escape_fts5_query(query: str) -> str:
    """Экранирует пользовательский запрос для FTS5 MATCH.

    FTS5 трактует ?, ", *, -, :, (, ), ^ как операторы.
    Разбиваем запрос на слова, обёртываем каждое в двойные кавычки —
    получается phrase-query per-token, сохраняя неявный AND между термами.
    Пустой результат → '' (вызывающий код пропускает MATCH-условие).
    """
    tokens = _FTS5_SPECIAL.sub(" ", query).split()
    if not tokens:
        return ""
    return " ".join(f'"{tok}"' for tok in tokens)
