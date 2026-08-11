"""Реранкинг результатов гибридного поиска (T-217, S-27).

bge-reranker-v2-m3 переупорядочивает top-50 кандидатов, отбирает top-8.
Оценки сохраняются для трассировки. Деградация при недоступности реранкера —
возвращается top-8 по RRF-скору с предупреждением, запрос не падает.

arch.md §8.2 шаг 4: реранкинг (top-8).
S-27: пары «запрос, чанк» обрабатываются пачками, оценки в span, замер времени.

FlagEmbedding (extras [full]) — тот же пакет, что для эмбеддингов (T-211).
FlagReranker доступен в зафиксированной версии 1.4.0.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.rag.embeddings import detect_device
from app.rag.hybrid_search import HybridResult

logger = logging.getLogger(__name__)

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


@dataclass(frozen=True)
class RerankResult:
    """Результат реранкинга — один чанк с новой оценкой.

    chunk_id — UUID чанка (String(36)).
    score — оценка реранкера (больше = релевантнее). В degraded mode — RRF-скор.
    text — текст чанка.
    original_rank — позиция в merged до реранкинга, 1-based (1 = первое место).
    """

    chunk_id: str
    score: float
    text: str
    original_rank: int


@dataclass(frozen=True)
class RerankOutput:
    """Выход реранкинга — top-k результатов + метаданные.

    results — отсортированы по score по убыванию, обрезаны до top_k.
    degraded — True если реранкер недоступен или упал, используется RRF-порядок.
    duration_ms — время работы шага в миллисекундах.
    error — описание ошибки если degraded=True, иначе None.
    """

    results: list[RerankResult]
    degraded: bool
    duration_ms: float
    error: str | None


class LocalReranker:
    """Локальный реранкер через FlagEmbedding (bge-reranker-v2-m3).

    Требует extras [full] (тянет torch). Lazy import — модуль не загружается
    если не установлен. Device auto-detection через общую detect_device().
    """

    def __init__(self, model_name: str = RERANKER_MODEL, device: str | None = None) -> None:
        try:
            from FlagEmbedding import FlagReranker  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "FlagEmbedding не установлен. Установите orqion[full]: pip install orqion[full]"
            ) from e

        self._model_name = model_name
        self._device = device or detect_device()
        self._reranker = FlagReranker(
            model_name,
            use_fp16=self._device.startswith("cuda"),
            device=self._device,
        )

    def compute_score(self, pairs: list[list[str]]) -> list[float]:
        """Синхронный вызов FlagReranker.compute_score.

        pairs: список [query, document] пар.
        Returns: список float-оценок (больше = релевантнее).
        """
        scores: list[float] = self._reranker.compute_score(pairs, normalize=True)
        return [float(s) for s in scores]


def create_reranker(model_name: str = RERANKER_MODEL) -> LocalReranker | None:
    """Фабрика реранкера. Возвращает None если FlagEmbedding не установлен.

    None означает degraded mode — rerank() вернёт top-k по RRF-скору.
    """
    try:
        return LocalReranker(model_name)
    except ImportError:
        logger.info("Reranker not available (FlagEmbedding not installed) — degraded mode")
        return None


async def rerank(
    query: str,
    candidates: list[HybridResult],
    reranker: LocalReranker | None,
    top_k: int = 8,
) -> RerankOutput:
    """Реранкинг кандидатов гибридного поиска.

    Если reranker недоступен (None) или падает при вычислении — degradation:
    возвращается top-k по RRF-скору с degraded=True.

    Args:
        query: текст запроса.
        candidates: список HybridResult из hybrid_search (отсортированы по RRF).
        reranker: LocalReranker или None (degraded mode).
        top_k: количество результатов (8 по умолчанию).

    Returns:
        RerankOutput с top-k результатов и метаданными.
    """
    start = time.perf_counter()

    # Пустой список — пустой результат
    if not candidates:
        return RerankOutput(
            results=[],
            degraded=False,
            duration_ms=0.0,
            error=None,
        )

    # Degraded mode — реранкер недоступен
    if reranker is None:
        duration_ms = (time.perf_counter() - start) * 1000
        results = _top_k_by_rrf(candidates, top_k)
        return RerankOutput(
            results=results,
            degraded=True,
            duration_ms=duration_ms,
            error="Reranker not available (FlagEmbedding not installed)",
        )

    # Попытка реранкинга
    try:
        pairs = [[query, c.text] for c in candidates]
        scores = await asyncio.to_thread(reranker.compute_score, pairs)

        # Сортировка по оценке реранкера, обрезка до top_k
        indexed = list(zip(scores, candidates, strict=True))
        indexed.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, candidate in indexed[:top_k]:
            results.append(
                RerankResult(
                    chunk_id=candidate.chunk_id,
                    score=score,
                    text=candidate.text,
                    original_rank=_original_rank(candidates, candidate.chunk_id),
                )
            )

        duration_ms = (time.perf_counter() - start) * 1000
        return RerankOutput(
            results=results,
            degraded=False,
            duration_ms=duration_ms,
            error=None,
        )

    except Exception as exc:  # noqa: BLE001 — граница системы: torch/FlagReranker
        # Деградация при runtime-ошибке реранкера
        logger.warning("Reranker failed, degrading to RRF order: %s", exc)
        duration_ms = (time.perf_counter() - start) * 1000
        results = _top_k_by_rrf(candidates, top_k)
        return RerankOutput(
            results=results,
            degraded=True,
            duration_ms=duration_ms,
            error=f"Reranker runtime error: {exc}",
        )


def _top_k_by_rrf(candidates: list[HybridResult], top_k: int) -> list[RerankResult]:
    """Top-k по RRF-скору — degradation при недоступности реранкера."""
    results: list[RerankResult] = []
    for i, candidate in enumerate(candidates[:top_k]):
        results.append(
            RerankResult(
                chunk_id=candidate.chunk_id,
                score=candidate.score,
                text=candidate.text,
                original_rank=i + 1,
            )
        )
    return results


def _original_rank(candidates: list[HybridResult], chunk_id: str) -> int:
    """Возвращает 1-based ранг chunk_id в candidates (до реранкинга)."""
    for i, c in enumerate(candidates):
        if c.chunk_id == chunk_id:
            return i + 1
    return 0
