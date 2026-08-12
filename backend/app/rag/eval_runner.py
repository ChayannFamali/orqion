"""Прогон оценки (T-225).

arch.md §8.4: метрики recall@k, MRR, доля процитированных источников,
доля обоснованных отказов, задержка по шагам.
Приёмка: прогон воспроизводим; состав конвейера и версия индекса зафиксированы.

pipeline_config: не только имена шагов, но и алиасы моделей (generate_model_alias,
rewrite_model_alias если rewrite включён, reranker_enabled) — для воспроизводимости.

grounded_refusal_ratio: эвристическая проверка содержимого ответа на маркеры
отказа («не найдено», «нет информации» и т.д.). Ограничение: LLM формулирует
по-разному, эвристика неточна — помечена в коде и метриках.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, EvalItem, EvalRun, EvalSet, Model, Provider
from app.rag.pipeline import PIPELINE, RagContext, RagState, run_pipeline

logger = logging.getLogger("orqion.rag.eval_runner")

K_VALUES = [1, 3, 5, 10]

# Эвристика для grounded_refusal_ratio (неточная, ADR-13-подобное ограничение).
# Маркеры отказа — сопоставимо с системной инструкцией T-219.
_REFUSAL_MARKERS = [
    "не найден",
    "не найдена",
    "не найдено",
    "нет информации",
    "не удалось найти",
    "не содержится",
    "нет данных",
    "не хватает информации",
    "insufficient information",
    "not found",
    "no information",
    "cannot find",
    "no relevant",
]


def _is_grounded_refusal(answer: str, fragments_used: int) -> bool:
    """Эвристическая проверка: ответ — обоснованный отказ.

    Условия:
    1. fragments_used == 0 (нет материала в контексте).
    2. answer содержит маркер отказа (case-insensitive).

    Ограничение: LLM формулирует отказы по-разному, список маркеров неполон.
    Эвристика может давать false negatives (модель отказалась другими словами)
    и false positives (модель упомянула «не найдено» в другом контексте).
    Не выдаётся за точный расчёт.
    """
    if fragments_used > 0:
        return False
    if not answer:
        return False
    answer_lower = answer.lower()
    return any(marker in answer_lower for marker in _REFUSAL_MARKERS)


@dataclass
class ItemResult:
    """Результат одного вопроса в прогоне."""

    question: str
    expected_doc_ids: list[str]
    retrieved_doc_ids: list[str]  # document_id для каждого retrieved chunk (top-k)
    answer: str | None
    fragments_used: int
    sources_count: int
    is_refusal: bool


def compute_metrics(
    items: list[ItemResult],
    k_values: list[int] | None = None,
) -> dict[str, object]:
    """Вычисляет метрики качества поиска и генерации.

    Метрики:
    - recall@k: доля вопросов, где хотя бы один expected_doc_id в top-k retrieved.
    - MRR: среднее 1/rank первого релевантного результата.
    - cited_sources_ratio: доля вопросов с хотя бы одним источником в ответе.
    - grounded_refusal_ratio: доля вопросов с 0 hits, где модель честно отказалась.
    - latency_by_step: задержка по шагам (передаётся отдельно из trace).

    Args:
        items: результаты всех вопросов.
        k_values: список k для recall@k (по умолчанию [1, 3, 5, 10]).

    Returns:
        dict с метриками. Значения — float [0, 1].
    """
    if k_values is None:
        k_values = K_VALUES

    total = len(items)
    if total == 0:
        return {
            "recall@1": 0.0,
            "recall@3": 0.0,
            "recall@5": 0.0,
            "recall@10": 0.0,
            "mrr": 0.0,
            "cited_sources_ratio": 0.0,
            "grounded_refusal_ratio": 0.0,
            "total_items": 0,
        }

    # recall@k
    recall: dict[int, float] = {}
    for k in k_values:
        hits = 0
        for item in items:
            top_k = item.retrieved_doc_ids[:k]
            if any(doc_id in item.expected_doc_ids for doc_id in top_k):
                hits += 1
        recall[k] = hits / total

    # MRR
    reciprocal_ranks: list[float] = []
    for item in items:
        rr = 0.0
        for rank, doc_id in enumerate(item.retrieved_doc_ids, 1):
            if doc_id in item.expected_doc_ids:
                rr = 1.0 / rank
                break
        reciprocal_ranks.append(rr)
    mrr = sum(reciprocal_ranks) / total

    # cited_sources_ratio
    cited = sum(1 for item in items if item.sources_count > 0)
    cited_ratio = cited / total

    # grounded_refusal_ratio
    no_hits_items = [item for item in items if item.fragments_used == 0]
    if no_hits_items:
        refusals = sum(1 for item in no_hits_items if item.is_refusal)
        refusal_ratio = refusals / len(no_hits_items)
    else:
        refusal_ratio = 0.0

    metrics: dict[str, object] = {
        "total_items": total,
        "mrr": round(mrr, 4),
        "cited_sources_ratio": round(cited_ratio, 4),
        "grounded_refusal_ratio": round(refusal_ratio, 4),
    }
    for k in k_values:
        metrics[f"recall@{k}"] = round(recall[k], 4)

    return metrics


def build_pipeline_config(
    steps: list[str] | None = None,
    generate_model_alias: str = "",
    rewrite_model_alias: str | None = None,
    reranker_enabled: bool = True,
) -> dict[str, object]:
    """Строит pipeline_config для записи в eval_run.

    Фиксирует не только шаги, но и модели — для воспроизводимости.
    """
    step_names = steps if steps is not None else [s.__name__ if hasattr(s, "__name__") else str(s) for s in PIPELINE]
    config: dict[str, object] = {
        "steps": step_names,
        "generate_model_alias": generate_model_alias,
        "reranker_enabled": reranker_enabled,
    }
    if rewrite_model_alias is not None:
        config["rewrite_model_alias"] = rewrite_model_alias
    return config


async def run_eval(
    session: AsyncSession,
    workspace_id: str,
    eval_set_id: str,
    index_version_id: str,
    settings: Any,
    vector_store: Any,
    embedding_backend: Any,
    secret_key: str,
    model: Model,
    provider: Provider,
    steps: list[str] | None = None,
) -> EvalRun:
    """Прогон оценки: для каждого вопроса запускает RAG pipeline, считает метрики.

    Args:
        session: async DB session.
        workspace_id: workspace ID.
        eval_set_id: ID набора оценки.
        index_version_id: ID версии индекса.
        settings: Settings instance.
        vector_store: VectorStore instance.
        embedding_backend: EmbeddingBackend instance.
        secret_key: secret key for provider.
        model: Model for generate step.
        provider: Provider for generate step.
        steps: override pipeline steps (None = default PIPELINE).

    Returns:
        EvalRun record with metrics.
    """
    # Загружаем элементы набора
    item_result = await session.execute(
        select(EvalItem).where(
            EvalItem.eval_set_id == eval_set_id,
            EvalItem.workspace_id == workspace_id,
        )
    )
    eval_items = list(item_result.scalars().all())

    # Загружаем eval_set для corpus_id
    es_result = await session.execute(
        select(EvalSet).where(EvalSet.id == eval_set_id)
    )
    eval_set = es_result.scalar_one_or_none()
    if eval_set is None:
        raise ValueError(f"Eval set {eval_set_id} not found")

    # Загружаем все чанки для index_version (для маппинга chunk_id → document_id)
    chunk_result = await session.execute(
        select(Chunk).where(Chunk.index_version_id == index_version_id)
    )
    all_chunks = list(chunk_result.scalars().all())
    chunk_to_doc: dict[str, str] = {c.id: c.document_id for c in all_chunks}

    # Pipeline config
    pipeline_config = build_pipeline_config(
        steps=steps,
        generate_model_alias=model.alias,
        rewrite_model_alias=getattr(settings, "rag_reformulation_model_alias", None)
        if getattr(settings, "rag_query_reformulation_enabled", False)
        else None,
        reranker_enabled=True,
    )

    # Прогон каждого вопроса
    item_results: list[ItemResult] = []
    for eval_item in eval_items:
        rag_state = RagState(query=eval_item.question, trace_id=f"eval-{eval_set_id}")
        rag_ctx = RagContext(
            session=session,
            settings=settings,
            vector_store=vector_store,
            embedding_backend=embedding_backend,
            secret_key=secret_key,
            workspace_id=workspace_id,
            index_version_id=index_version_id,
            model=model,
            provider=provider,
        )

        try:
            rag_state = await run_pipeline(rag_state, rag_ctx)
        except Exception as exc:  # noqa: BLE001  граница системы
            logger.error("Eval item %s failed: %s", eval_item.id, exc)
            rag_state.degraded = True
            rag_state.errors.append(f"eval_item: {exc}")

        # retrieved_doc_ids из reranked (до truncation)
        retrieved_chunk_ids = [r.chunk_id for r in rag_state.reranked]
        retrieved_doc_ids = [chunk_to_doc.get(cid, "") for cid in retrieved_chunk_ids]

        item_results.append(
            ItemResult(
                question=eval_item.question,
                expected_doc_ids=eval_item.expected_doc_ids,
                retrieved_doc_ids=retrieved_doc_ids,
                answer=rag_state.answer,
                fragments_used=rag_state.fragments_used,
                sources_count=len(rag_state.sources),
                is_refusal=_is_grounded_refusal(rag_state.answer or "", rag_state.fragments_used),
            )
        )

    # Вычисляем метрики
    metrics = compute_metrics(item_results)

    # Сохраняем прогон
    eval_run = EvalRun(
        workspace_id=workspace_id,
        eval_set_id=eval_set_id,
        index_version_id=index_version_id,
        pipeline=pipeline_config,
        metrics=metrics,
    )
    session.add(eval_run)
    await session.flush()

    return eval_run
