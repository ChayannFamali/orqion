"""RAG-конвейер (T-220).

arch.md §8.3, ADR-11: линейный конвейер из чистых функций, каждый шаг
трассируется, последовательность шагов — данные (подменяемость).

Шаги: rewrite → search → rerank → build_context → generate.

Примечание: RagState — dataclass, не pydantic BaseModel (отступление от §8.3).
Обоснование: единообразие с остальным RAG-модулем, где DocChunk, CodeChunk,
RerankResult, RewriteResult, ContextOutput — dataclasses.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Chunk, Model, Provider
from app.providers.client import ProviderClient
from app.rag.context_builder import ContextOutput, build_context
from app.rag.embeddings import EmbeddingBackend
from app.rag.hybrid_search import HybridResult, HybridSearchOutput, hybrid_search, rrf
from app.rag.query_rewrite import maybe_rewrite_query
from app.rag.reranker import RerankOutput, create_reranker, rerank
from app.rag.sources import SourceEntry, build_sources
from app.rag.vector_store import VectorStore
from app.trace.service import TraceContext, span

logger = logging.getLogger("orqion.rag.pipeline")


@dataclass
class RagState:
    """Состояние конвейера. arch.md §8.3 (dataclass вместо BaseModel)."""

    query: str
    trace_id: str
    rewritten: str | None = None
    hits: list[HybridResult] = field(default_factory=list)
    reranked: list[Any] = field(default_factory=list)  # list[RerankResult]
    context: str | None = None
    fragments_used: int = 0
    answer: str | None = None
    degraded: bool = False
    errors: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    sources: list[SourceEntry] = field(default_factory=list)
    # T-439: атрибуция чанков корпусам — chunk_id → (corpus_id, corpus_name)
    chunk_corpus: dict[str, tuple[str, str]] = field(default_factory=dict)
    # T-307: данные для панели трассировки
    search_candidates: list[dict[str, object]] = field(default_factory=list)
    rerank_results: list[dict[str, object]] = field(default_factory=list)


# T-439: потолок объединённого списка после второго RRF перед реранком.
# Контракт реранкера (T-217, «50→8») не меняется — через него проходит
# объединённый top-50 вместо одиночного корпусного.
MULTI_RRF_TOP_K = 50


@dataclass
class RagContext:
    """Зависимости, внедряемые в каждый шаг (не в state)."""

    session: AsyncSession
    settings: Settings
    vector_store: VectorStore
    embedding_backend: EmbeddingBackend
    secret_key: str
    workspace_id: str
    index_version_id: str
    model: Model
    provider: Provider
    trace_ctx: TraceContext | None = None
    messages: list[dict[str, str]] | None = None
    reranker: Any = None  # LocalReranker | None
    # T-439: мульти-корпусный режим. Если список задан и длиннее одного —
    # поиск идёт по каждой версии, результаты сливаются вторым RRF.
    # corpus_attribution: index_version_id → (corpus_id, corpus_name).
    index_version_ids: list[str] = field(default_factory=list)
    corpus_attribution: dict[str, tuple[str, str]] = field(default_factory=dict)


RagStep = Callable[[RagState, RagContext], Awaitable[RagState]]


async def step_rewrite(state: RagState, ctx: RagContext) -> RagState:
    """Переформулировка запроса (T-218)."""
    messages = ctx.messages or [{"role": "user", "content": state.query}]
    result = await maybe_rewrite_query(
        ctx.session,
        ctx.settings,
        messages,
        ctx.secret_key,
        ctx.workspace_id,
        ctx.trace_ctx,
    )
    state.rewritten = result.query
    if result.degraded:
        state.degraded = True
        if result.error:
            state.errors.append(f"rewrite: {result.error}")
    return state


async def step_search(state: RagState, ctx: RagContext) -> RagState:
    """Гибридный поиск (T-216). T-439: по нескольким версиям — двухуровневый
    RRF (решение В1 дизайн-ревью): гибрид по каждому корпусу с k=50, затем
    второй RRF по корпусным merged-спискам с обрезкой до MULTI_RRF_TOP_K.
    """
    search_query = state.rewritten or state.query
    version_ids = ctx.index_version_ids or [ctx.index_version_id]

    if len(version_ids) <= 1:
        output: HybridSearchOutput = await hybrid_search(
            ctx.vector_store,
            ctx.embedding_backend,
            version_ids[0],
            search_query,
            k=50,
        )
        state.hits = output.merged
        attribution = ctx.corpus_attribution.get(version_ids[0])
        if attribution is not None:
            state.chunk_corpus = {h.chunk_id: attribution for h in output.merged}
    else:
        outputs: list[HybridSearchOutput] = []
        for version_id in version_ids:
            outputs.append(
                await hybrid_search(
                    ctx.vector_store,
                    ctx.embedding_backend,
                    version_id,
                    search_query,
                    k=50,
                )
            )
        # Второй уровень: каждый корпусный merged — отдельный ранжированный
        # вход. Скоры плотного/разреженного поиска разных версий индексов
        # несравнимы, поэтому пулинг хитов отклонён дизайн-ревью.
        rankings = [[h.chunk_id for h in out.merged] for out in outputs]
        fused_scores = rrf(rankings)

        text_map: dict[str, str] = {}
        for version_id, out in zip(version_ids, outputs):
            attribution = ctx.corpus_attribution.get(version_id)
            for h in out.merged:
                text_map.setdefault(h.chunk_id, h.text)
                if attribution is not None:
                    state.chunk_corpus.setdefault(h.chunk_id, attribution)

        ordered = sorted(fused_scores.items(), key=lambda kv: kv[1], reverse=True)[:MULTI_RRF_TOP_K]
        state.hits = [
            HybridResult(
                chunk_id=chunk_id,
                score=score,
                text=text_map.get(chunk_id, ""),
                dense_rank=None,
                sparse_rank=None,
            )
            for chunk_id, score in ordered
        ]

    # T-307: кандидаты поиска для панели трассировки
    state.search_candidates = [
        {
            "chunk_id": h.chunk_id,
            "score": h.score,
            "dense_rank": h.dense_rank,
            "sparse_rank": h.sparse_rank,
        }
        for h in state.hits
    ]
    return state


async def step_rerank(state: RagState, ctx: RagContext) -> RagState:
    """Реранкинг (T-217)."""
    reranker = ctx.reranker
    if reranker is None:
        reranker = create_reranker()
    output: RerankOutput = await rerank(
        query=state.rewritten or state.query,
        candidates=state.hits,
        reranker=reranker,
        top_k=8,
    )
    state.reranked = output.results
    # T-307: результаты реранкинга для панели трассировки
    state.rerank_results = [
        {
            "chunk_id": r.chunk_id,
            "score": r.score,
            "original_rank": r.original_rank,
        }
        for r in output.results
    ]
    if output.degraded:
        state.degraded = True
        if output.error:
            state.errors.append(f"rerank: {output.error}")
    return state


async def step_build_context(state: RagState, ctx: RagContext) -> RagState:
    """Сборка контекста (T-219). Загружает Chunk из БД по reranked chunk_id."""
    chunk_ids = [r.chunk_id for r in state.reranked]
    chunks: list[Chunk] = []
    if chunk_ids:
        result = await ctx.session.execute(select(Chunk).where(Chunk.id.in_(chunk_ids)))
        chunks = list(result.scalars().all())

    max_tokens = ctx.model.max_input_tokens or 32000

    output: ContextOutput = await build_context(
        reranked=state.reranked,
        chunks=chunks,
        max_tokens=max_tokens,
        query=state.rewritten or state.query,
        trace_ctx=ctx.trace_ctx,
    )
    state.context = output.context
    state.fragments_used = output.fragments_used
    state.sources = build_sources(
        included_chunk_ids=output.included_chunk_ids,
        reranked=state.reranked,
        chunks=chunks,
        corpus_by_chunk=state.chunk_corpus or None,
    )
    if output.truncated:
        state.degraded = True
        state.errors.append(
            f"build_context: truncated, {output.fragments_skipped_oversized} skipped oversized"
        )
    return state


async def step_generate(state: RagState, ctx: RagContext) -> RagState:
    """Генерация ответа через ProviderClient.complete (T-111/T-112).

    Всегда вызывается, даже при 0 фрагментов — модель сама скажет
    «не найдено в материале» per системной инструкции (T-219).
    """
    context = state.context or ""
    messages = [
        {"role": "system", "content": context},
        {"role": "user", "content": state.rewritten or state.query},
    ]
    client = ProviderClient(ctx.provider, ctx.secret_key)
    result = await client.complete(
        messages=messages,
        model=ctx.model.upstream_name,
        max_tokens=ctx.model.max_output_tokens,
        temperature=0.7,
    )
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    state.usage = result.get("usage")
    state.answer = content if content else None
    if not state.answer:
        state.degraded = True
        state.errors.append("generate: empty response")
    return state


PIPELINE: list[RagStep] = [
    step_rewrite,
    step_search,
    step_rerank,
    step_build_context,
    step_generate,
]


async def run_pipeline(
    state: RagState,
    ctx: RagContext,
    steps: Sequence[RagStep] | None = None,
) -> RagState:
    """Запуск конвейера. Каждый шаг обёрнут в span.

    Шаг может выбросить Exception — конвейер продолжается, ошибка пишется в state.
    """
    pipeline = steps if steps is not None else PIPELINE

    for step in pipeline:
        step_name = step.__name__ if hasattr(step, "__name__") else str(step)
        span_payload: dict[str, object] = {"step": step_name}

        async with (
            span(ctx.trace_ctx, step_name, payload=span_payload) if ctx.trace_ctx else _NullSpan()
        ):
            try:
                state = await step(state, ctx)
            except Exception as exc:  # noqa: BLE001  граница системы
                state.degraded = True
                error_msg = str(exc) or exc.__class__.__name__
                state.errors.append(f"{step_name}: {error_msg}")
                logger.warning("Pipeline step %s failed: %s", step_name, error_msg)

        span_payload["degraded"] = state.degraded
        span_payload["errors"] = list(state.errors)

        # T-307: обогащение payload данными шага для панели трассировки
        if step_name == "step_search":
            span_payload["candidates_count"] = len(state.search_candidates)
            span_payload["candidates"] = state.search_candidates
        elif step_name == "step_rerank":
            span_payload["reranked_count"] = len(state.rerank_results)
            span_payload["reranked"] = state.rerank_results

    return state


class _NullSpan:
    """No-op async context manager когда trace_ctx не передан."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None
