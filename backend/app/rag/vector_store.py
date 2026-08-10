"""Векторное хранилище: встроенное (sqlite-vec + FTS5) (T-212, S-25).

sqlite-vec для плотного поиска (cosine similarity через L2 на нормализованных векторах),
FTS5 для разреженного (BM25). Фильтрация по index_version_id на стороне хранилища.
Удаление версии индекса освобождает место (drop_version).

Без внешних сервисов — профиль minimal. Qdrant — T-213, тот же Protocol.

Важно для T-214 (pipeline индексации):
- rowid в vec_chunks и fts_chunks — auto-assigned INTEGER, не chunk.id.
- chunk.id в проекте — String(36) (UUID), несовместим с vec0 rowid (INTEGER).
- T-214 должен строить маппинг rowid ↔ chunk.id (отдельная таблица или словарь).
- Hit.chunk_id возвращает rowid, не chunk.id — T-214 разрешает через маппинг.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import aiosqlite

from app.rag.embeddings import EmbeddedChunk

# ---------------------------------------------------------------------------
# Контракты
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    """Результат поиска — чанк с оценкой релевантности."""

    chunk_id: int
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

    async def _get_conn(self) -> aiosqlite.Connection:
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
        await conn.commit()

        self._conn = conn
        return conn

    async def upsert(self, index_version_id: str, chunks: Sequence[EmbeddedChunk]) -> None:
        """Запись чанков: векторы в vec_chunks, текст в fts_chunks.

        rowid auto-assigned SQLite — глобально уникальный.
        Маппинг rowid → chunk.id на стороне вызывающего (T-214 pipeline).
        fts_chunks.rowid = vec_chunks.rowid для JOIN.
        """
        conn = await self._get_conn()

        for chunk in chunks:
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

        await conn.commit()

    async def search_dense(self, index_version_id: str, vec: list[float], k: int = 10) -> list[Hit]:
        """Плотный поиск: cosine similarity через L2 на нормализованных векторах."""
        conn = await self._get_conn()
        vec_bytes = struct.pack(f"{len(vec)}f", *vec)

        cursor = await conn.execute(
            "SELECT vec_chunks.rowid, vec_chunks.distance, fts_chunks.text "
            "FROM vec_chunks "
            "JOIN fts_chunks ON fts_chunks.rowid = vec_chunks.rowid "
            "WHERE vec_chunks.index_version_id = ? "
            "AND fts_chunks.index_version_id = ? "
            "AND vec_chunks.embedding MATCH ? "
            "AND k = ? "
            "ORDER BY vec_chunks.distance",
            (index_version_id, index_version_id, vec_bytes, k),
        )
        rows = await cursor.fetchall()

        # distance — L2 (меньше = лучше), score = 1 - distance (больше = лучше)
        return [Hit(chunk_id=row[0], score=1.0 - row[1], text=row[2]) for row in rows]

    async def search_sparse(self, index_version_id: str, query: str, k: int = 10) -> list[Hit]:
        """Разреженный поиск: BM25 через FTS5."""
        conn = await self._get_conn()

        # FTS5 MATCH с фильтрацией по index_version_id
        cursor = await conn.execute(
            "SELECT fts_chunks.rowid, bm25(fts_chunks), fts_chunks.text "
            "FROM fts_chunks "
            "WHERE fts_chunks.index_version_id = ? "
            "AND fts_chunks.text MATCH ? "
            "ORDER BY bm25(fts_chunks) "
            "LIMIT ?",
            (index_version_id, query, k),
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
        await conn.commit()

        # Освобождение дискового пространства
        await conn.execute("PRAGMA incremental_vacuum")
        await conn.commit()

    async def close(self) -> None:
        """Закрытие соединения."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
