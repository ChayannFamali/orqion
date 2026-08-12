"""Схемы запроса и ответа для eval-сущностей (T-223).

Минимальный набор: Create/Read для EvalSet, EvalItem, EvalRun.
API-эндпоинты появятся в T-224 (импорт) и T-225 (прогон).
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
