"""Тесты прогона оценки (T-225).

Проверки:
- test_compute_metrics_all_relevant: все вопросы — релевантный ответ в top-1
- test_compute_metrics_no_relevant: ни один вопрос не нашёл эталон
- test_compute_metrics_partial: часть вопросов нашла эталон
- test_compute_metrics_recall_at_k: recall@1 < recall@5 < recall@10
- test_compute_metrics_mrr: MRR корректно для разных позиций
- test_compute_metrics_cited_sources_ratio: доля вопросов с источниками
- test_compute_metrics_grounded_refusal: эвристика отказа при 0 hits
- test_compute_metrics_grounded_refusal_no_marker: ответ без маркера отказа — не refusal
- test_compute_metrics_grounded_refusal_with_fragments: fragments>0 — не refusal
- test_compute_metrics_empty: пустой список вопросов
- test_is_grounded_refusal_markers: разные формулировки отказа
- test_build_pipeline_config: состав шагов + алиасы моделей
- test_build_pipeline_config_with_rewrite: rewrite_model_alias при включённом rewrite
"""

from __future__ import annotations

from app.rag.eval_runner import (
    ItemResult,
    _is_grounded_refusal,
    build_pipeline_config,
    compute_metrics,
)

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _item(
    question: str = "Q",
    expected_doc_ids: list[str] | None = None,
    retrieved_doc_ids: list[str] | None = None,
    answer: str | None = "Answer",
    fragments_used: int = 1,
    sources_count: int = 1,
) -> ItemResult:
    return ItemResult(
        question=question,
        expected_doc_ids=expected_doc_ids or [],
        retrieved_doc_ids=retrieved_doc_ids or [],
        answer=answer,
        fragments_used=fragments_used,
        sources_count=sources_count,
        is_refusal=_is_grounded_refusal(answer or "", fragments_used),
    )


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------


def test_compute_metrics_all_relevant() -> None:
    """Все вопросы — эталон в top-1 → recall@k=1.0, MRR=1.0."""
    items = [
        _item(expected_doc_ids=["d1"], retrieved_doc_ids=["d1", "d2", "d3"]),
        _item(expected_doc_ids=["d2"], retrieved_doc_ids=["d2", "d4"]),
    ]
    metrics = compute_metrics(items)
    assert metrics["recall@1"] == 1.0
    assert metrics["recall@3"] == 1.0
    assert metrics["recall@5"] == 1.0
    assert metrics["recall@10"] == 1.0
    assert metrics["mrr"] == 1.0


def test_compute_metrics_no_relevant() -> None:
    """Ни один вопрос не нашёл эталон → recall@k=0.0, MRR=0.0."""
    items = [
        _item(expected_doc_ids=["d1"], retrieved_doc_ids=["d2", "d3"]),
        _item(expected_doc_ids=["d2"], retrieved_doc_ids=["d4"]),
    ]
    metrics = compute_metrics(items)
    assert metrics["recall@1"] == 0.0
    assert metrics["recall@10"] == 0.0
    assert metrics["mrr"] == 0.0


def test_compute_metrics_partial() -> None:
    """1 из 2 нашёл эталон → recall@k=0.5."""
    items = [
        _item(expected_doc_ids=["d1"], retrieved_doc_ids=["d1"]),
        _item(expected_doc_ids=["d2"], retrieved_doc_ids=["d3"]),
    ]
    metrics = compute_metrics(items)
    assert metrics["recall@1"] == 0.5
    assert metrics["recall@10"] == 0.5


def test_compute_metrics_recall_at_k() -> None:
    """recall@1 < recall@3 < recall@5 — эталон на разных позициях."""
    items = [
        # Эталон в top-1
        _item(expected_doc_ids=["d1"], retrieved_doc_ids=["d1", "d2", "d3"]),
        # Эталон в top-3 (позиция 2)
        _item(expected_doc_ids=["d2"], retrieved_doc_ids=["d3", "d2", "d4"]),
        # Эталон в top-5 (позиция 4)
        _item(expected_doc_ids=["d5"], retrieved_doc_ids=["d6", "d7", "d8", "d9", "d5"]),
    ]
    metrics = compute_metrics(items)
    assert float(metrics["recall@1"]) < float(metrics["recall@3"])  # type: ignore[arg-type]
    assert float(metrics["recall@3"]) < float(metrics["recall@5"])  # type: ignore[arg-type]


def test_compute_metrics_mrr() -> None:
    """MRR: 1/1 + 1/3 + 0 = 0.667 (для 3 вопросов)."""
    items = [
        _item(expected_doc_ids=["d1"], retrieved_doc_ids=["d1", "d2"]),  # rank 1
        _item(expected_doc_ids=["d3"], retrieved_doc_ids=["d4", "d5", "d3"]),  # rank 3
        _item(expected_doc_ids=["d6"], retrieved_doc_ids=["d7", "d8"]),  # not found
    ]
    metrics = compute_metrics(items)
    assert metrics["mrr"] == round((1.0 + 1 / 3 + 0.0) / 3, 4)


def test_compute_metrics_cited_sources_ratio() -> None:
    """Доля вопросов с источниками: 2 из 3."""
    items = [
        _item(sources_count=2),
        _item(sources_count=1),
        _item(sources_count=0, fragments_used=0, answer="Not found"),
    ]
    metrics = compute_metrics(items)
    assert metrics["cited_sources_ratio"] == round(2 / 3, 4)


def test_compute_metrics_grounded_refusal() -> None:
    """Эвристика отказа: 0 hits + маркер отказа → grounded_refusal_ratio."""
    items = [
        _item(fragments_used=0, answer="Информация не найдена в материале"),
        _item(fragments_used=0, answer="Нет информации по данному запросу"),
        _item(fragments_used=0, answer="The information is not found in the provided documents"),
    ]
    metrics = compute_metrics(items)
    assert metrics["grounded_refusal_ratio"] == 1.0


def test_compute_metrics_grounded_refusal_no_marker() -> None:
    """0 hits, но ответ без маркера отказа → не refusal (галлюцинация)."""
    items = [
        _item(fragments_used=0, answer="The answer is 42."),
    ]
    metrics = compute_metrics(items)
    assert metrics["grounded_refusal_ratio"] == 0.0


def test_compute_metrics_grounded_refusal_with_fragments() -> None:
    """fragments>0 — не считается для refusal ratio."""
    items = [
        _item(fragments_used=2, answer="Не найдено в материале"),
    ]
    metrics = compute_metrics(items)
    # Нет вопросов с 0 hits → refusal_ratio = 0
    assert metrics["grounded_refusal_ratio"] == 0.0


def test_compute_metrics_mixed_refusal() -> None:
    """Смешанный: 1 отказ, 1 галлюцинация → ratio=0.5."""
    items = [
        _item(fragments_used=0, answer="Информация не найдена"),
        _item(fragments_used=0, answer="The answer is definitely 42."),
    ]
    metrics = compute_metrics(items)
    assert metrics["grounded_refusal_ratio"] == 0.5


def test_compute_metrics_empty() -> None:
    """Пустой список → все метрики 0.0."""
    metrics = compute_metrics([])
    assert metrics["total_items"] == 0
    assert metrics["recall@1"] == 0.0
    assert metrics["mrr"] == 0.0


# ---------------------------------------------------------------------------
# _is_grounded_refusal
# ---------------------------------------------------------------------------


def test_is_grounded_refusal_markers() -> None:
    """Разные формулировки отказа распознаются."""
    assert _is_grounded_refusal("Информация не найдена в материале", 0)
    assert _is_grounded_refusal("Нет информации по данному вопросу", 0)
    assert _is_grounded_refusal("Не удалось найти релевантный материал", 0)
    assert _is_grounded_refusal("Insufficient information to answer", 0)
    assert _is_grounded_refusal("No relevant documents found", 0)


def test_is_grounded_refusal_false_cases() -> None:
    """Не отказ: fragments>0, пустой ответ, ответ без маркера."""
    assert not _is_grounded_refusal("Не найдено", 1)  # fragments>0
    assert not _is_grounded_refusal("", 0)  # пустой ответ
    assert not _is_grounded_refusal("The answer is 42", 0)  # нет маркера


# ---------------------------------------------------------------------------
# build_pipeline_config
# ---------------------------------------------------------------------------


def test_build_pipeline_config() -> None:
    """pipeline_config содержит шаги и алиас модели."""
    config = build_pipeline_config(
        generate_model_alias="local/test-model",
        reranker_enabled=True,
    )
    assert "steps" in config
    assert config["generate_model_alias"] == "local/test-model"
    assert config["reranker_enabled"] is True
    assert "rewrite_model_alias" not in config


def test_build_pipeline_config_with_rewrite() -> None:
    """pipeline_config содержит rewrite_model_alias при включённом rewrite."""
    config = build_pipeline_config(
        generate_model_alias="local/test-model",
        rewrite_model_alias="local/rewrite-model",
        reranker_enabled=True,
    )
    assert config["rewrite_model_alias"] == "local/rewrite-model"


def test_build_pipeline_config_custom_steps() -> None:
    """Кастомные шаги сохраняются."""
    config = build_pipeline_config(
        steps=["search", "rerank", "generate"],
        generate_model_alias="local/test-model",
    )
    assert config["steps"] == ["search", "rerank", "generate"]
