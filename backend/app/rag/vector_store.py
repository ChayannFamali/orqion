"""Векторное хранилище: встроенное (sqlite-vec + FTS5) (T-212, S-25).

sqlite-vec для плотного поиска (cosine similarity через L2 на нормализованных векторах),
FTS5 для разреженного (BM25). Фильтрация по index_version_id на стороне хранилища.
Удаление версии индекса освобождает место (drop_version).

Без внешних сервисов — профиль minimal. Qdrant — T-213, тот же Protocol.

chunk.id в проекте — String(36) (UUID). vec0 требует INTEGER rowid.
SQLiteVectorStore скрывает это несоответствие: внутренняя таблица vec_chunk_map
хранит маппинг rowid ↔ chunk_id. upsert принимает chunk_id, search_* возвращает
chunk_id — реальный идентификатор чанка в основной БД.
"""

from __future__ import annotations

import asyncio
import os
import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import aiosqlite

from app.rag.embeddings import EmbeddedChunk

# ---------------------------------------------------------------------------
# FTS5 query escaping (BUG-003)
# ---------------------------------------------------------------------------

# FTS5 спецсимволы: " * - : ( ) ? ^ ! & |
_FTS5_SPECIAL = re.compile(r'["*\-:()?!&|]')


def _escape_fts5_query(query: str) -> str:
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


# ---------------------------------------------------------------------------
# Контракты
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    """Результат поиска — чанк с оценкой релевантности.

    chunk_id — UUID из таблицы chunk (String(36)), не внутренний rowid.
    """

    chunk_id: str
    score: float
    text: str


@runtime_checkable
class VectorStore(Protocol):
    """Единый протокол хранилища векторов. Реализации: SQLite (T-212), Qdrant (T-213)."""

    async def upsert(self, index_version_id: str, chunks: Sequence[EmbeddedChunk]) -> None:
        """Запись чанков с векторами для версии индекса."""
        ...

    async def search_dense(self, index_version_id: str, vec: list[float], k: int = 10) -> list[Hit]:
        """Плотный поиск по вектору (cosine similarity)."""
        ...

    async def search_sparse(self, index_version_id: str, query: str, k: int = 10) -> list[Hit]:
        """Разреженный поиск (BM25/FTS5)."""
        ...

    async def drop_version(self, index_version_id: str) -> None:
        """Удаление всех векторов и FTS-записей версии индекса."""
        ...


# ---------------------------------------------------------------------------
# SQLiteVectorStore — sqlite-vec + FTS5
# ---------------------------------------------------------------------------

# Размерность bge-m3 = 1024
EMBEDDING_DIM = 1024


def _loadable_path() -> str:
    """Возвращает путь к sqlite-vec loadable, с расширением под платформу."""
    import sqlite_vec

    path = str(sqlite_vec.loadable_path())
    if not os.path.exists(path):
        for ext in (".dll", ".so", ".dylib"):
            candidate = path + ext
            if os.path.exists(candidate):
                return str(candidate)
    return path


class SQLiteVectorStore:
    """Векторное хранилище на sqlite-vec (dense) + FTS5 (sparse).

    Без внешних сервисов — профиль minimal. Векторы хранятся в vec0 virtual table,
    текст в FTS5. Фильтрация по index_version_id — в SQL WHERE.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._conn_lock = asyncio.Lock()

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is not None:
            return self._conn

        async with self._conn_lock:
            # Double-check после захвата lock — другой task мог создать соединение
            if self._conn is not None:
                return self._conn

            conn = await aiosqlite.connect(self._db_path)

            # PRAGMA auto_vacuum = INCREMENTAL — позволяет drop_version
            # освобождать дисковое пространство через PRAGMA incremental_vacuum.
            # Должно быть установлено до создания таблиц (SQLite хранит в заголовке БД).
            await conn.execute("PRAGMA auto_vacuum = INCREMENTAL")

            await conn.enable_load_extension(True)
            await conn.load_extension(_loadable_path())

            # Создание таблиц если не существуют
            await conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
                f"USING vec0(embedding float[{EMBEDDING_DIM}], "
                f"index_version_id text)"
            )
            await conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks "
                "USING fts5(text, index_version_id UNINDEXED)"
            )
            # Маппинг rowid ↔ chunk_id. rowid — auto-assigned в vec_chunks/fts_chunks,
            # chunk_id — UUID из основной таблицы chunk (String(36)).
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS vec_chunk_map "
                "(rowid INTEGER PRIMARY KEY, chunk_id TEXT NOT NULL, "
                "index_version_id TEXT NOT NULL)"
            )
            await conn.commit()

            self._conn = conn
            return conn

    async def upsert(self, index_version_id: str, chunks: Sequence[EmbeddedChunk]) -> None:
        """Запись чанков: векторы в vec_chunks, текст в fts_chunks, маппинг в vec_chunk_map.

        chunk_id (UUID) берётся из EmbeddedChunk.chunk_id. rowid auto-assigned SQLite.
        Таблица vec_chunk_map хранит соответствие rowid ↔ chunk_id.
        """
        conn = await self._get_conn()

        for chunk in chunks:
            if not chunk.chunk_id:
                raise ValueError("EmbeddedChunk.chunk_id должен быть заполнен (UUID)")

            vec_bytes = struct.pack(f"{len(chunk.vector)}f", *chunk.vector)
            # INSERT в vec_chunks — rowid auto-assigned
            cursor = await conn.execute(
                "INSERT INTO vec_chunks(embedding, index_version_id) VALUES (?, ?)",
                (vec_bytes, index_version_id),
            )
            rowid = cursor.lastrowid
            # INSERT в fts_chunks с тем же rowid
            await conn.execute(
                "INSERT INTO fts_chunks(rowid, text, index_version_id) VALUES (?, ?, ?)",
                (rowid, chunk.text, index_version_id),
            )
            # Маппинг rowid → chunk_id
            await conn.execute(
                "INSERT INTO vec_chunk_map(rowid, chunk_id, index_version_id) VALUES (?, ?, ?)",
                (rowid, chunk.chunk_id, index_version_id),
            )

        await conn.commit()

    async def search_dense(self, index_version_id: str, vec: list[float], k: int = 10) -> list[Hit]:
        """Плотный поиск: cosine similarity через L2 на нормализованных векторах."""
        conn = await self._get_conn()
        vec_bytes = struct.pack(f"{len(vec)}f", *vec)

        cursor = await conn.execute(
            "SELECT m.chunk_id, v.distance, f.text "
            "FROM vec_chunks v "
            "JOIN fts_chunks f ON f.rowid = v.rowid "
            "JOIN vec_chunk_map m ON m.rowid = v.rowid "
            "WHERE v.index_version_id = ? "
            "AND f.index_version_id = ? "
            "AND m.index_version_id = ? "
            "AND v.embedding MATCH ? "
            "AND k = ? "
            "ORDER BY v.distance",
            (index_version_id, index_version_id, index_version_id, vec_bytes, k),
        )
        rows = await cursor.fetchall()

        # distance — L2 (меньше = лучше), score = 1 - distance (больше = лучше)
        return [Hit(chunk_id=row[0], score=1.0 - row[1], text=row[2]) for row in rows]

    async def search_sparse(self, index_version_id: str, query: str, k: int = 10) -> list[Hit]:
        """Разреженный поиск: BM25 через FTS5."""
        conn = await self._get_conn()

        # BUG-003: экранируем спецсимволы FTS5 в пользовательском запросе.
        fts_query = _escape_fts5_query(query)
        if fts_query:
            cursor = await conn.execute(
                "SELECT m.chunk_id, bm25(fts_chunks), fts_chunks.text "
                "FROM fts_chunks "
                "JOIN vec_chunk_map m ON m.rowid = fts_chunks.rowid "
                "WHERE fts_chunks.index_version_id = ? "
                "AND m.index_version_id = ? "
                "AND fts_chunks.text MATCH ? "
                "ORDER BY bm25(fts_chunks) "
                "LIMIT ?",
                (index_version_id, index_version_id, fts_query, k),
            )
        else:
            # Пустой FTS-запрос (только спецсимволы) — без MATCH, просто top-k
            cursor = await conn.execute(
                "SELECT m.chunk_id, 0.0, fts_chunks.text "
                "FROM fts_chunks "
                "JOIN vec_chunk_map m ON m.rowid = fts_chunks.rowid "
                "WHERE fts_chunks.index_version_id = ? "
                "AND m.index_version_id = ? "
                "LIMIT ?",
                (index_version_id, index_version_id, k),
            )
        rows = await cursor.fetchall()

        # bm25() возвращает отрицательный score (меньше = лучше)
        # конвертируем: score = -bm25 (больше = лучше)
        return [Hit(chunk_id=row[0], score=-row[1], text=row[2]) for row in rows]

    async def drop_version(self, index_version_id: str) -> None:
        """Удаление всех векторов и FTS-записей версии индекса.

        Освобождает дисковое пространство через PRAGMA incremental_vacuum
        (auto_vacuum = INCREMENTAL установлен при создании БД).
        Не блокирует как полный VACUUM — освобождает страницы постепенно.
        """
        conn = await self._get_conn()

        await conn.execute(
            "DELETE FROM vec_chunks WHERE index_version_id = ?",
            (index_version_id,),
        )
        await conn.execute(
            "DELETE FROM fts_chunks WHERE index_version_id = ?",
            (index_version_id,),
        )
        await conn.execute(
            "DELETE FROM vec_chunk_map WHERE index_version_id = ?",
            (index_version_id,),
        )
        await conn.commit()

        # Освобождение дискового пространства
        await conn.execute("PRAGMA incremental_vacuum")
        await conn.commit()

    async def close(self) -> None:
        """Закрытие соединения."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
