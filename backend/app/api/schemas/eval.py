"""Схемы запроса и ответа для eval-сущностей (T-223, T-224, T-225).

T-223: базовые Create/Read для EvalSet, EvalItem, EvalRun.
T-224: расширение для API импорта — CreateWithItems, ReadWithItems, ListResponse.
T-225: расширение для прогона — EvalRunCreate, EvalRunListResponse.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class EvalSetCreate(BaseModel):
    corpus_id: str
    name: str


class EvalSetRead(BaseModel):
    id: str
    workspace_id: str
    corpus_id: str
    name: str
    created_at: datetime


class EvalItemCreate(BaseModel):
    question: str
    expected_doc_ids: list[str] = []
    expected_answer: str | None = None


class EvalItemRead(BaseModel):
    id: str
    workspace_id: str
    eval_set_id: str
    question: str
    expected_doc_ids: list[str]
    expected_answer: str | None


class EvalRunRead(BaseModel):
    id: str
    workspace_id: str
    eval_set_id: str
    index_version_id: str | None
    pipeline: dict[str, object]
    metrics: dict[str, object] | None
    ts: datetime


# T-224: расширение для API импорта


class EvalSetCreateWithItems(BaseModel):
    """Создание набора с элементами (POST /api/corpora/{corpus_id}/eval-sets)."""

    name: str
    items: list[EvalItemCreate]


class EvalSetReadWithItems(BaseModel):
    """Набор с элементами (GET /api/eval-sets/{id})."""

    id: str
    workspace_id: str
    corpus_id: str
    name: str
    created_at: datetime
    items: list[EvalItemRead]


class EvalSetListResponse(BaseModel):
    """Список наборов корпуса."""

    items: list[EvalSetRead]


class EvalImportResponse(BaseModel):
    """Результат импорта (POST /api/eval-sets/{id}/import)."""

    eval_set_id: str
    total_items: int
    matched_items: int


# T-225: прогон оценки


class EvalRunCreate(BaseModel):
    """Запуск прогона (POST /api/eval-sets/{id}/runs)."""

    index_version_id: str
    steps: list[str] | None = None


class EvalRunListResponse(BaseModel):
    """Список прогонов набора."""

    items: list[EvalRunRead]


# T-226: сравнение прогонов


class EvalCompareRequest(BaseModel):
    """Запрос сравнения прогонов (POST /api/eval-runs/compare)."""

    run_ids: list[str]


class RunMetadataRead(BaseModel):
    """Метаданные одного прогона в сравнении."""

    run_id: str
    ts: str
    index_version_id: str | None
    generate_model_alias: str
    rewrite_model_alias: str | None
    reranker_enabled: bool
    steps: list[str]


class MetricDeltaRead(BaseModel):
    """Дельта одной метрики между двумя прогонами."""

    metric_name: str
    earlier_value: float
    later_value: float
    delta: float
    direction: str


class EvalComparisonRead(BaseModel):
    """Результат сравнения прогонов."""

    eval_set_id: str
    runs: list[RunMetadataRead]
    deltas: list[MetricDeltaRead]
