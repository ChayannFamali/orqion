"""Тесты реранкинга (T-217, S-27).

Проверки:
- test_rerank_basic: StubReranker, 50 → 8, отсортированы по скору
- test_rerank_degraded_on_no_reranker: reranker=None → degradation, top-8 по RRF
- test_rerank_degraded_on_runtime_error: реранкер падает → degradation
- test_rerank_duration_measured: duration_ms > 0
- test_rerank_empty_candidates: пустой список → пустой результат
- test_rerank_preserves_chunk_id_and_text: chunk_id и text из HybridResult
- test_rerank_original_rank: original_rank — позиция в merged до реранкинга (1-based)
"""

from __future__ import annotations

import pytest
from app.rag.hybrid_search import HybridResult
from app.rag.reranker import (
    _top_k_by_rrf,
    rerank,
)

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class StubReranker:
    """Заглушка реранкера для тестов — детерминированные скоры.

    Присваивает более высокий скор чанкам с более длинным text
    (не имеет ML-смысла, но детерминированно и тестируемо).
    """

    def compute_score(self, pairs: list[list[str]]) -> list[float]:
        return [float(len(doc)) for _query, doc in pairs]


class FailingReranker:
    """Реранкер, который всегда бросает RuntimeError."""

    def compute_score(self, pairs: list[list[str]]) -> list[float]:
        raise RuntimeError("Simulated reranker failure")


def _make_candidates(n: int) -> list[HybridResult]:
    """Создаёт n HybridResult с убывающим RRF-скором."""
    results: list[HybridResult] = []
    for i in range(n):
        results.append(
            HybridResult(
                chunk_id=f"chunk-{i:04d}",
                score=1.0 / (60 + i + 1),  # убывающий RRF-скор
                text=f"text content {i}",
                dense_rank=i + 1 if i < n // 2 else None,
                sparse_rank=i + 1 if i >= n // 2 else None,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_basic() -> None:
    """StubReranker, 50 кандидатов → 8 результатов, отсортированы по скору."""
    candidates = _make_candidates(50)
    stub = StubReranker()

    output = await rerank("query", candidates, stub, top_k=8)  # type: ignore[arg-type]

    assert len(output.results) == 8
    assert not output.degraded
    assert output.error is None

    # Отсортированы по score по убыванию
    for i in range(len(output.results) - 1):
        assert output.results[i].score >= output.results[i + 1].score

    # Все chunk_id уникальны
    ids = {r.chunk_id for r in output.results}
    assert len(ids) == 8


@pytest.mark.asyncio
async def test_rerank_degraded_on_no_reranker() -> None:
    """reranker=None → degradation, top-8 по RRF, degraded=True."""
    candidates = _make_candidates(50)

    output = await rerank("query", candidates, None, top_k=8)

    assert output.degraded is True
    assert "not available" in (output.error or "")
    assert len(output.results) == 8

    # Результаты — top-8 по RRF (порядок не изменился)
    for i, result in enumerate(output.results):
        assert result.chunk_id == f"chunk-{i:04d}"
        assert result.original_rank == i + 1


@pytest.mark.asyncio
async def test_rerank_degraded_on_runtime_error() -> None:
    """Реранкер падает → degradation, original order сохранён."""
    candidates = _make_candidates(20)
    failing = FailingReranker()

    output = await rerank("query", candidates, failing, top_k=8)  # type: ignore[arg-type]

    assert output.degraded is True
    assert "runtime error" in (output.error or "").lower()
    assert len(output.results) == 8

    # Результаты — top-8 по RRF (degradation сохранила порядок)
    for i, result in enumerate(output.results):
        assert result.chunk_id == f"chunk-{i:04d}"


@pytest.mark.asyncio
async def test_rerank_duration_measured() -> None:
    """duration_ms > 0."""
    candidates = _make_candidates(10)
    stub = StubReranker()

    output = await rerank("query", candidates, stub, top_k=5)  # type: ignore[arg-type]

    assert output.duration_ms > 0


@pytest.mark.asyncio
async def test_rerank_empty_candidates() -> None:
    """Пустой список кандидатов → пустой результат, не degraded."""
    output = await rerank("query", [], None, top_k=8)

    assert output.results == []
    assert output.degraded is False
    assert output.error is None


@pytest.mark.asyncio
async def test_rerank_preserves_chunk_id_and_text() -> None:
    """chunk_id и text из HybridResult в RerankResult."""
    candidates = [
        HybridResult(
            chunk_id="test-uuid-001",
            score=0.05,
            text="hello world content",
            dense_rank=1,
            sparse_rank=None,
        ),
        HybridResult(
            chunk_id="test-uuid-002",
            score=0.04,
            text="foo bar baz qux",
            dense_rank=2,
            sparse_rank=None,
        ),
    ]
    stub = StubReranker()

    output = await rerank("query", candidates, stub, top_k=2)  # type: ignore[arg-type]

    # chunk_id и text сохранены
    ids = {r.chunk_id for r in output.results}
    assert "test-uuid-001" in ids
    assert "test-uuid-002" in ids

    for result in output.results:
        assert result.text != ""
        # text соответствует chunk_id
        if result.chunk_id == "test-uuid-001":
            assert result.text == "hello world content"
        elif result.chunk_id == "test-uuid-002":
            assert result.text == "foo bar baz qux"


@pytest.mark.asyncio
async def test_rerank_original_rank() -> None:
    """original_rank — позиция в merged до реранкинга (1-based)."""
    candidates = _make_candidates(10)
    stub = StubReranker()

    output = await rerank("query", candidates, stub, top_k=10)  # type: ignore[arg-type]

    # original_rank — позиция в candidates (1-based)
    # StubReranker сортирует по len(text), все text одинаковой длины →
    # порядок может не измениться, но original_rank должен указывать
    # на исходную позицию
    for result in output.results:
        # original_rank должен быть в диапазоне 1..10
        assert 1 <= result.original_rank <= 10
        # chunk_id соответствует original_rank
        expected_idx = result.original_rank - 1
        assert result.chunk_id == candidates[expected_idx].chunk_id


@pytest.mark.asyncio
async def test_rerank_degraded_preserves_original_rank() -> None:
    """В degraded mode original_rank — позиция в RRF-списке (1-based)."""
    candidates = _make_candidates(10)

    output = await rerank("query", candidates, None, top_k=5)

    for i, result in enumerate(output.results):
        assert result.original_rank == i + 1


# ---------------------------------------------------------------------------
# _top_k_by_rrf — чистая функция
# ---------------------------------------------------------------------------


def test_top_k_by_rrf_basic() -> None:
    """_top_k_by_rrf возвращает top-k по порядку RRF."""
    candidates = _make_candidates(10)
    results = _top_k_by_rrf(candidates, top_k=3)

    assert len(results) == 3
    assert results[0].chunk_id == "chunk-0000"
    assert results[1].chunk_id == "chunk-0001"
    assert results[2].chunk_id == "chunk-0002"

    # score — RRF-скор из HybridResult
    assert results[0].score == candidates[0].score


def test_top_k_by_rrf_empty() -> None:
    """Пустой список — пустой результат."""
    assert _top_k_by_rrf([], top_k=5) == []


def test_top_k_by_rrf_k_larger_than_candidates() -> None:
    """k больше чем кандидатов — возвращаются все."""
    candidates = _make_candidates(3)
    results = _top_k_by_rrf(candidates, top_k=10)

    assert len(results) == 3
