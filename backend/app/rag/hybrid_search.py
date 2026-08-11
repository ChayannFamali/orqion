"""Гибридный поиск: параллельный dense + sparse, слияние RRF (T-216, S-26).

Параллельный запуск search_dense и search_sparse через asyncio.gather,
слияние алгоритмом RRF (Reciprocal Rank Fusion, k=60), дедупликация по chunk_id.
Оба списка сохраняются в выходной структуре для трассировки (T-307).

arch.md §8.2: плотный вектор (bge-m3) и разреженный BM25 выполняются параллельно,
результаты сливаются алгоритмом RRF. BM25 обязателен: находит точные имена
функций, номера договоров и артикулы, где эмбеддинги неточны.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.rag.embeddings import EmbeddingBackend
from app.rag.vector_store import Hit, VectorStore

# Константа RRF — стандартное значение из литературы (S-26).
RRF_K = 60


@dataclass(frozen=True)
class HybridResult:
    """Результат гибридного поиска после слияния RRF.

    chunk_id — UUID чанка (String(36)).
    score — RRF-скор (больше = релевантнее).
    text — текст чанка из Hit (из dense или sparse списка).
    dense_rank — ранг в dense-списке, 1-based (1 = первое место).
                 None если чанк отсутствует в dense-результатах.
    sparse_rank — ранг в sparse-списке, 1-based (1 = первое место).
                  None если чанк отсутствует в sparse-результатах.
    """

    chunk_id: str
    score: float
    text: str
    dense_rank: int | None
    sparse_rank: int | None


@dataclass(frozen=True)
class HybridSearchOutput:
    """Выход гибридного поиска — оба списка + финальный смешанный результат.

    dense_hits и sparse_hits сохраняются отдельно для трассировки (T-307).
    merged — отсортирован по RRF-скору по убыванию, обрезан до k.
    """

    dense_hits: list[Hit]
    sparse_hits: list[Hit]
    merged: list[HybridResult]


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion.

    Сливает несколько ранжированных списков в один скор.
    score(chunk_id) = sum(1 / (k + rank)) по всем спискам, где chunk_id присутствует.

    Args:
        rankings: список ранжированных списков chunk_id.
        k: константа сглаживания (60 — стандарт).

    Returns:
        dict[chunk_id → rrf_score], не отсортирован.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


async def hybrid_search(
    vector_store: VectorStore,
    embedding_backend: EmbeddingBackend,
    index_version_id: str,
    query: str,
    k: int = 50,
) -> HybridSearchOutput:
    """Параллельный dense + sparse поиск, слияние RRF, дедупликация.

    1. search_dense(k) и search_sparse(k) запускаются параллельно через asyncio.gather.
    2. RRF сливает ранги, k=60.
    3. Дедупликация по chunk_id.
    4. merged сортируется по RRF-скору по убыванию, обрезается до k.

    Args:
        vector_store: VectorStore (SQLite или Qdrant).
        embedding_backend: EmbeddingBackend для векторизации запроса.
        index_version_id: версия индекса для поиска.
        query: текст запроса.
        k: количество результатов (top-k). Оба поиска запускаются с k,
           merged обрезается до k после RRF.

    Returns:
        HybridSearchOutput с dense_hits, sparse_hits и merged.
    """
    # 1. Векторизация запроса
    embeddings = await embedding_backend.embed([query])
    query_vec = embeddings[0]

    # 2. Параллельный запуск dense и sparse
    dense_hits, sparse_hits = await asyncio.gather(
        vector_store.search_dense(index_version_id, query_vec, k=k),
        vector_store.search_sparse(index_version_id, query, k=k),
    )

    # 3. RRF
    dense_ids = [h.chunk_id for h in dense_hits]
    sparse_ids = [h.chunk_id for h in sparse_hits]
    rrf_scores = rrf([dense_ids, sparse_ids])

    # 4. Маппинг chunk_id → Hit (из обоих списков)
    hit_map: dict[str, Hit] = {}
    for hit in dense_hits:
        hit_map[hit.chunk_id] = hit
    for hit in sparse_hits:
        if hit.chunk_id not in hit_map:
            hit_map[hit.chunk_id] = hit

    # 5. Ранги (1-based)
    dense_rank_map = {h.chunk_id: i + 1 for i, h in enumerate(dense_hits)}
    sparse_rank_map = {h.chunk_id: i + 1 for i, h in enumerate(sparse_hits)}

    # 6. Сборка merged
    merged: list[HybridResult] = []
    for chunk_id, score in rrf_scores.items():
        found: Hit | None = hit_map.get(chunk_id)
        text = found.text if found is not None else ""
        merged.append(
            HybridResult(
                chunk_id=chunk_id,
                score=score,
                text=text,
                dense_rank=dense_rank_map.get(chunk_id),
                sparse_rank=sparse_rank_map.get(chunk_id),
            )
        )

    # 7. Сортировка по RRF-скору по убыванию, обрезка до k
    merged.sort(key=lambda r: r.score, reverse=True)
    merged = merged[:k]

    return HybridSearchOutput(
        dense_hits=dense_hits,
        sparse_hits=sparse_hits,
        merged=merged,
    )
