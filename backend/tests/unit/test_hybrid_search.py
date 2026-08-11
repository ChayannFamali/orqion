"""Тесты гибридного поиска (T-216, S-26).

Проверки:
- rrf: базовое слияние, дедупликация, пустые списки, один список
- hybrid_search: параллельный dense+sparse, RRF слияние
- test_hybrid_search_finds_exact_function_name: BM25 находит точное имя функции
- test_hybrid_search_output_capped_at_k: merged обрезан до k
- test_hybrid_search_preserves_both_lists: dense_hits и sparse_hits доступны отдельно
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
from app.rag.embeddings import EmbeddedChunk
from app.rag.hybrid_search import hybrid_search, rrf
from app.rag.vector_store import EMBEDDING_DIM, Hit, SQLiteVectorStore

# ---------------------------------------------------------------------------
# RRF — чистая функция
# ---------------------------------------------------------------------------


def test_rrf_basic() -> None:
    """RRF на двух списках с пересечением."""
    rankings = [
        ["a", "b", "c"],
        ["b", "a", "d"],
    ]
    scores = rrf(rankings, k=60)

    # "a" — ранг 1 в первом, ранг 2 во втором
    assert scores["a"] == 1.0 / (60 + 1) + 1.0 / (60 + 2)
    # "b" — ранг 2 в первом, ранг 1 во втором
    assert scores["b"] == 1.0 / (60 + 2) + 1.0 / (60 + 1)
    # "c" — ранг 3 только в первом
    assert scores["c"] == 1.0 / (60 + 3)
    # "d" — ранг 3 только во втором
    assert scores["d"] == 1.0 / (60 + 3)


def test_rrf_deduplication() -> None:
    """Один chunk_id в обоих списках — одна запись в scores."""
    rankings = [["a", "b"], ["a", "c"]]
    scores = rrf(rankings)
    # "a" встречается в обоих, но в scores одна запись
    assert len(scores) == 3
    # "a" получает сумму от обоих списков
    assert scores["a"] > scores["b"]
    assert scores["a"] > scores["c"]


def test_rrf_empty_lists() -> None:
    """Пустые списки — пустой результат."""
    assert rrf([]) == {}
    assert rrf([[], []]) == {}


def test_rrf_single_list() -> None:
    """Один список без второго."""
    scores = rrf([["a", "b"]])
    assert scores["a"] == 1.0 / (60 + 1)
    assert scores["b"] == 1.0 / (60 + 2)


def test_rrf_custom_k() -> None:
    """RRF с пользовательской k."""
    scores = rrf([["a"]], k=10)
    assert scores["a"] == 1.0 / (10 + 1)


# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class StubEmbeddingBackend:
    """Детерминированные векторы для тестов."""

    def __init__(self, model: str = "test-embed") -> None:
        self._model = model

    def model_name(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            vec = [0.0] * EMBEDDING_DIM
            idx = abs(hash(text)) % EMBEDDING_DIM
            vec[idx] = 1.0
            results.append(vec)
        return results


def _make_unit_vec(idx: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[idx] = 1.0
    return vec


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def vector_store(tmp_path: Path) -> SQLiteVectorStore:
    return SQLiteVectorStore(str(tmp_path / "test_hybrid.db"))


@pytest.fixture
def embedding_backend() -> StubEmbeddingBackend:
    return StubEmbeddingBackend()


@pytest.fixture(autouse=True)
async def _close_vector_store(vector_store: SQLiteVectorStore) -> AsyncIterator[None]:
    """Закрывает соединение vector_store после теста."""
    yield
    await vector_store.close()


async def _seed_version(
    store: SQLiteVectorStore,
    version: str,
    chunks: list[tuple[str, str, list[float]]],
) -> None:
    """Заполняет vector_store чанками: (chunk_id, text, vector)."""
    embedded = [
        EmbeddedChunk(text=text, vector=vec, ordinal=i, model="test", chunk_id=chunk_id)
        for i, (chunk_id, text, vec) in enumerate(chunks)
    ]
    await store.upsert(version, embedded)


# ---------------------------------------------------------------------------
# hybrid_search — интеграционные тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_search_parallel(
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """Параллельный dense+sparse, RRF слияние — базовый сценарий."""
    version = "ver-001"
    await _seed_version(
        vector_store,
        version,
        [
            ("chunk-a", "hello world", _make_unit_vec(0)),
            ("chunk-b", "foo bar baz", _make_unit_vec(1)),
            ("chunk-c", "hello foo", _make_unit_vec(2)),
        ],
    )

    output = await hybrid_search(vector_store, embedding_backend, version, "hello", k=10)

    # Оба списка непустые
    assert len(output.dense_hits) > 0
    assert len(output.sparse_hits) > 0

    # merged непустой, отсортирован по RRF-скору по убыванию
    assert len(output.merged) > 0
    for i in range(len(output.merged) - 1):
        assert output.merged[i].score >= output.merged[i + 1].score

    # Все chunk_id в merged уникальны (дедупликация)
    ids = {r.chunk_id for r in output.merged}
    assert len(ids) == len(output.merged)


@pytest.mark.asyncio
async def test_hybrid_search_finds_exact_function_name(
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """BM25 находит точное имя функции, где эмбеддинги неточны.

    Ключевой тест: чанк кода с функцией calculate_total.
    Dense-поиск по слову 'calculate' может не найти точное совпадение,
    но sparse (BM25) находит по точному имени. RRF гарантирует попадание
    в финальный результат с непустым text.
    """
    version = "ver-001"
    await _seed_version(
        vector_store,
        version,
        [
            # Чанк с точным именем функции
            ("chunk-func", "def calculate_total(items):\n    return sum(items)", _make_unit_vec(3)),
            # Чанк с другим содержанием, но близкий по dense-вектору
            ("chunk-decoy", "summary of monthly expenses report", _make_unit_vec(4)),
            # Ещё один код-чанк
            ("chunk-other", "def process_data(data):\n    return data", _make_unit_vec(5)),
        ],
    )

    output = await hybrid_search(vector_store, embedding_backend, version, "calculate_total", k=10)

    # chunk-func найден через sparse (BM25) по точному имени
    merged_ids = {r.chunk_id for r in output.merged}
    assert "chunk-func" in merged_ids

    # Найденный результат имеет непустой text
    func_result = next(r for r in output.merged if r.chunk_id == "chunk-func")
    assert "calculate_total" in func_result.text
    assert func_result.text != ""

    # chunk-func присутствует в sparse_hits (BM25 нашёл по точному имени)
    sparse_ids = {h.chunk_id for h in output.sparse_hits}
    assert "chunk-func" in sparse_ids

    # sparse_rank не None (чанк найден через sparse)
    assert func_result.sparse_rank is not None


@pytest.mark.asyncio
async def test_hybrid_search_output_capped_at_k(
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """merged обрезан до k, даже если dense и sparse почти не пересекаются."""
    version = "ver-001"
    # 10 чанков с уникальными векторами (dense) и уникальными словами (sparse)
    chunks = []
    for i in range(10):
        chunks.append((f"chunk-{i}", f"word{i} unique{i}", _make_unit_vec(i)))
    await _seed_version(vector_store, version, chunks)

    # k=5 — merged должен быть обрезан до 5
    output = await hybrid_search(vector_store, embedding_backend, version, "word0", k=5)

    assert len(output.merged) <= 5


@pytest.mark.asyncio
async def test_hybrid_search_preserves_both_lists(
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """dense_hits и sparse_hits доступны отдельно в output."""
    version = "ver-001"
    await _seed_version(
        vector_store,
        version,
        [
            ("chunk-a", "hello world", _make_unit_vec(0)),
            ("chunk-b", "foo bar", _make_unit_vec(1)),
        ],
    )

    output = await hybrid_search(vector_store, embedding_backend, version, "hello", k=10)

    # dense_hits — список Hit
    assert all(isinstance(h, Hit) for h in output.dense_hits)
    assert all(isinstance(h, Hit) for h in output.sparse_hits)

    # dense_hits и sparse_hits могут отличаться по составу
    dense_ids = {h.chunk_id for h in output.dense_hits}
    sparse_ids = {h.chunk_id for h in output.sparse_hits}

    # Оба непустые
    assert len(dense_ids) > 0
    assert len(sparse_ids) > 0

    # Объединение покрывает все chunk_id из merged
    merged_ids = {r.chunk_id for r in output.merged}
    assert merged_ids <= (dense_ids | sparse_ids)


@pytest.mark.asyncio
async def test_hybrid_search_dense_only_chunk_has_text(
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """Чанк найденный только через dense имеет непустой text в merged."""
    version = "ver-001"
    # chunk-only-dense имеет уникальный вектор, но текст без редких слов для sparse
    await _seed_version(
        vector_store,
        version,
        [
            ("chunk-only-dense", "the quick brown fox", _make_unit_vec(6)),
            ("chunk-both", "hello world foo", _make_unit_vec(7)),
        ],
    )

    output = await hybrid_search(vector_store, embedding_backend, version, "quick", k=10)

    # chunk-only-dense может быть найден через dense (вектор близок к "quick")
    dense_ids = {h.chunk_id for h in output.dense_hits}
    if "chunk-only-dense" in dense_ids:
        merged_result = next((r for r in output.merged if r.chunk_id == "chunk-only-dense"), None)
        assert merged_result is not None
        assert merged_result.text != ""
        assert merged_result.dense_rank is not None


@pytest.mark.asyncio
async def test_hybrid_search_empty_store(
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """Пустой vector store — пустой результат."""
    output = await hybrid_search(vector_store, embedding_backend, "ver-empty", "query", k=10)

    assert output.dense_hits == []
    assert output.sparse_hits == []
    assert output.merged == []
