"""Тесты векторного хранилища (T-212, S-25).

Проверки:
- upsert: запись векторов и текста
- search_dense: поиск по вектору, возвращает релевантные чанки
- search_sparse: FTS5 поиск по тексту, BM25 ранжирование
- drop_version: удаление освобождает место (search возвращает пусто после drop)
- Фильтрация по index_version_id: чанки другой версии не возвращаются
- chunk_id mapping: search_* возвращает UUID, не внутренний rowid
- upsert с пустым chunk_id — ValueError
- Protocol conformance
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from app.rag.embeddings import EmbeddedChunk
from app.rag.vector_store import EMBEDDING_DIM, SQLiteVectorStore, VectorStore


def _make_chunk(
    ordinal: int,
    text: str,
    vector: list[float],
    model: str = "test-model",
    chunk_id: str = "",
) -> EmbeddedChunk:
    """Создаёт EmbeddedChunk с заданным вектором и chunk_id."""
    if not chunk_id:
        chunk_id = f"chunk-{ordinal:04d}-uuid"
    return EmbeddedChunk(text=text, vector=vector, ordinal=ordinal, model=model, chunk_id=chunk_id)


def _make_unit_vec(dim: int, idx: int) -> list[float]:
    """Единичный вектор: 1.0 в позиции idx, 0.0 в остальных."""
    vec = [0.0] * dim
    vec[idx] = 1.0
    return vec


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Путь к временной SQLite базе."""
    return str(tmp_path / "test_vec.db")


@pytest.fixture
def store(db_path: str) -> SQLiteVectorStore:
    """SQLiteVectorStore на временной базе."""
    return SQLiteVectorStore(db_path)


@pytest.fixture
def chunks() -> list[EmbeddedChunk]:
    """3 чанка с ортогональными векторами."""
    return [
        _make_chunk(0, "hello world", _make_unit_vec(EMBEDDING_DIM, 0)),
        _make_chunk(1, "foo bar baz", _make_unit_vec(EMBEDDING_DIM, 1)),
        _make_chunk(2, "quick brown fox", _make_unit_vec(EMBEDDING_DIM, 2)),
    ]


# ---------------------------------------------------------------------------
# upsert + search_dense
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_search_dense(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """upsert + search_dense: ближайший вектор возвращается первым."""
    version = "ver-001"
    await store.upsert(version, chunks)

    # Ищем вектором, близким к chunks[0]
    query_vec = _make_unit_vec(EMBEDDING_DIM, 0)
    hits = await store.search_dense(version, query_vec, k=1)

    assert len(hits) == 1
    assert hits[0].text == "hello world"
    assert hits[0].chunk_id == "chunk-0000-uuid"
    # chunk_id — str (UUID), не int rowid
    assert isinstance(hits[0].chunk_id, str)
    # score = 1 - distance, distance ~0 для идентичного вектора
    assert hits[0].score > 0.99


@pytest.mark.asyncio
async def test_search_dense_returns_k(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """search_dense возвращает до k результатов."""
    version = "ver-001"
    await store.upsert(version, chunks)

    query_vec = _make_unit_vec(EMBEDDING_DIM, 0)
    hits = await store.search_dense(version, query_vec, k=2)

    assert len(hits) == 2
    # Первый — ближайший (chunks[0])
    assert hits[0].text == "hello world"


@pytest.mark.asyncio
async def test_search_dense_empty(store: SQLiteVectorStore) -> None:
    """search_dense на пустой версии — пустой список."""
    query_vec = _make_unit_vec(EMBEDDING_DIM, 0)
    hits = await store.search_dense("ver-empty", query_vec, k=10)
    assert hits == []


# ---------------------------------------------------------------------------
# search_sparse (FTS5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_sparse_basic(store: SQLiteVectorStore, chunks: list[EmbeddedChunk]) -> None:
    """search_sparse: FTS5 поиск по слову."""
    version = "ver-001"
    await store.upsert(version, chunks)

    hits = await store.search_sparse(version, "hello", k=10)

    assert len(hits) >= 1
    assert hits[0].text == "hello world"
    assert hits[0].chunk_id == "chunk-0000-uuid"
    assert isinstance(hits[0].chunk_id, str)


@pytest.mark.asyncio
async def test_search_sparse_no_match(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """search_sparse: нет совпадений — пустой список."""
    version = "ver-001"
    await store.upsert(version, chunks)

    hits = await store.search_sparse(version, "nonexistent", k=10)
    assert hits == []


@pytest.mark.asyncio
async def test_search_sparse_ranking(store: SQLiteVectorStore, chunks: list[EmbeddedChunk]) -> None:
    """search_sparse: BM25 ранжирование — точное совпадение выше."""
    version = "ver-001"
    extended_chunks = list(chunks) + [
        _make_chunk(3, "hello hello hello world", _make_unit_vec(EMBEDDING_DIM, 3)),
    ]
    await store.upsert(version, extended_chunks)

    hits = await store.search_sparse(version, "hello", k=10)
    # Чанк с бОльшим числом "hello" должен ранжироваться выше
    assert len(hits) >= 2
    assert hits[0].text == "hello hello hello world"  # больше вхождений "hello"


# ---------------------------------------------------------------------------
# BUG-003: FTS5 экранирование спецсимволов в пользовательских запросах
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_sparse_query_with_question_mark(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """search_sparse не падает на '?' в запросе (BUG-003)."""
    version = "ver-001"
    await store.upsert(version, chunks)

    hits = await store.search_sparse(version, "hello?", k=10)
    assert len(hits) >= 1
    assert hits[0].text == "hello world"


@pytest.mark.asyncio
async def test_search_sparse_query_with_double_quotes(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """search_sparse не падает на двойные кавычки в запросе (BUG-003)."""
    version = "ver-001"
    await store.upsert(version, chunks)

    hits = await store.search_sparse(version, '"hello" world', k=10)
    assert len(hits) >= 1


@pytest.mark.asyncio
async def test_search_sparse_query_with_asterisk(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """search_sparse не падает на '*' в запросе (BUG-003)."""
    version = "ver-001"
    await store.upsert(version, chunks)

    hits = await store.search_sparse(version, "hello*", k=10)
    assert len(hits) >= 1


@pytest.mark.asyncio
async def test_search_sparse_query_with_hyphen(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """search_sparse не падает на '-' в запросе (BUG-003)."""
    version = "ver-001"
    await store.upsert(version, chunks)

    hits = await store.search_sparse(version, "hello -world", k=10)
    assert len(hits) >= 1


@pytest.mark.asyncio
async def test_search_sparse_query_full_sentence_with_punctuation(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """search_sparse не падает на полный запрос с пунктуацией (BUG-003).

    Реальный пользовательский запрос: 'How to parse JSON in Python?'
    """
    version = "ver-001"
    sentence_chunks = [
        _make_chunk(0, "how to parse json in python", _make_unit_vec(EMBEDDING_DIM, 0)),
        _make_chunk(1, "hello world from python", _make_unit_vec(EMBEDDING_DIM, 1)),
    ]
    await store.upsert(version, sentence_chunks)

    hits = await store.search_sparse(version, "How to parse JSON in Python?", k=10)
    assert len(hits) >= 1
    # Первый результат — про парсинг JSON
    assert "parse" in hits[0].text


@pytest.mark.asyncio
async def test_search_sparse_query_only_special_chars(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """search_sparse не падает на запрос только из спецсимволов (BUG-003)."""
    version = "ver-001"
    await store.upsert(version, chunks)

    # Запрос только из спецсимволов → все токены пустые → '"*"' (match-all)
    hits = await store.search_sparse(version, '?*"-', k=10)
    # Не падает, возвращает результаты (match-all)
    assert len(hits) >= 1


# ---------------------------------------------------------------------------
# Фильтрация по index_version_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_by_index_version(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """Чанки другой версии не возвращаются в search_dense."""
    await store.upsert("ver-001", chunks)
    await store.upsert(
        "ver-002",
        [_make_chunk(0, "other version", _make_unit_vec(EMBEDDING_DIM, 0), chunk_id="other-uuid")],
    )

    query_vec = _make_unit_vec(EMBEDDING_DIM, 0)
    hits_v1 = await store.search_dense("ver-001", query_vec, k=10)
    hits_v2 = await store.search_dense("ver-002", query_vec, k=10)

    assert all(h.text != "other version" for h in hits_v1)
    assert all(h.text == "other version" for h in hits_v2)
    assert all(h.chunk_id != "other-uuid" for h in hits_v1)
    assert all(h.chunk_id == "other-uuid" for h in hits_v2)
    assert len(hits_v1) == 3
    assert len(hits_v2) == 1


@pytest.mark.asyncio
async def test_filter_sparse_by_index_version(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """Чанки другой версии не возвращаются в search_sparse."""
    await store.upsert("ver-001", chunks)
    await store.upsert(
        "ver-002",
        [_make_chunk(0, "hello other", _make_unit_vec(EMBEDDING_DIM, 0), chunk_id="other-uuid")],
    )

    hits_v1 = await store.search_sparse("ver-001", "hello", k=10)
    hits_v2 = await store.search_sparse("ver-002", "hello", k=10)

    assert all(h.text == "hello world" for h in hits_v1)
    assert all(h.text == "hello other" for h in hits_v2)


# ---------------------------------------------------------------------------
# drop_version — освобождает место
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drop_version_frees_space(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk], db_path: str
) -> None:
    """drop_version удаляет данные и освобождает дисковое пространство.

    Проверка: после drop_version размер файла БД уменьшается
    (PRAGMA incremental_vacuum освобождает страницы ОС).
    """
    version = "ver-001"
    # Заполняем векторами — повторяем для большего размера
    all_chunks = []
    for i in range(10):
        all_chunks.append(
            _make_chunk(
                i,
                f"text {i}",
                _make_unit_vec(EMBEDDING_DIM, i % 10),
                chunk_id=f"chunk-drop-{i:04d}",
            )
        )
    await store.upsert(version, all_chunks)

    # Проверяем, что данные есть
    query_vec = _make_unit_vec(EMBEDDING_DIM, 0)
    hits_before = await store.search_dense(version, query_vec, k=10)
    assert len(hits_before) == 10

    # Размер файла до удаления
    await store.close()
    size_before = os.path.getsize(db_path)
    await store._get_conn()  # переподключаемся

    # Удаляем версию
    await store.drop_version(version)

    # Размер файла после удаления
    await store.close()
    size_after = os.path.getsize(db_path)

    # Файл уменьшился — incremental_vacuum освободил страницы
    assert size_after < size_before, f"File size not reduced: {size_before} -> {size_after}"

    # Проверяем, что данных нет
    hits_after = await store.search_dense(version, query_vec, k=10)
    assert hits_after == []

    hits_sparse = await store.search_sparse(version, "text", k=10)
    assert hits_sparse == []


@pytest.mark.asyncio
async def test_drop_version_preserves_other(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """drop_version одной версии не затрагивает другую."""
    await store.upsert("ver-001", chunks)
    await store.upsert("ver-002", chunks)

    await store.drop_version("ver-001")

    query_vec = _make_unit_vec(EMBEDDING_DIM, 0)
    hits_v1 = await store.search_dense("ver-001", query_vec, k=10)
    hits_v2 = await store.search_dense("ver-002", query_vec, k=10)

    assert hits_v1 == []
    assert len(hits_v2) == 3


# ---------------------------------------------------------------------------
# chunk_id mapping (rowid ↔ UUID)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_requires_chunk_id(store: SQLiteVectorStore) -> None:
    """upsert с пустым chunk_id — ValueError."""
    chunk = EmbeddedChunk(
        text="no id",
        vector=_make_unit_vec(EMBEDDING_DIM, 0),
        ordinal=0,
        model="test",
        chunk_id="",
    )
    with pytest.raises(ValueError, match="chunk_id"):
        await store.upsert("ver-001", [chunk])


@pytest.mark.asyncio
async def test_chunk_id_mapping_roundtrip(
    store: SQLiteVectorStore, chunks: list[EmbeddedChunk]
) -> None:
    """search_dense и search_sparse возвращают настоящий chunk_id, не rowid."""
    version = "ver-001"
    await store.upsert(version, chunks)

    query_vec = _make_unit_vec(EMBEDDING_DIM, 0)
    hits_dense = await store.search_dense(version, query_vec, k=3)
    hits_sparse = await store.search_sparse(version, "hello", k=10)

    # Все chunk_id — строки UUID, не int
    expected_ids = {"chunk-0000-uuid", "chunk-0001-uuid", "chunk-0002-uuid"}
    dense_ids = {h.chunk_id for h in hits_dense}
    sparse_ids = {h.chunk_id for h in hits_sparse}

    assert dense_ids == expected_ids
    assert sparse_ids <= expected_ids  # sparse может вернуть subset
    assert all(isinstance(h.chunk_id, str) for h in hits_dense)
    assert all(isinstance(h.chunk_id, str) for h in hits_sparse)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_sqlite_vector_store_satisfies_protocol() -> None:
    """SQLiteVectorStore удовлетворяет VectorStore Protocol."""
    store = SQLiteVectorStore(":memory:")
    assert isinstance(store, VectorStore)


# ---------------------------------------------------------------------------
# Очистка
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _close_store_after_test(store: SQLiteVectorStore) -> AsyncGenerator[None]:
    """Закрывает соединение после теста."""
    yield
    await store.close()
