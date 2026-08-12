"""Pydantic-схемы для trace/span read API (T-307)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SpanResponse(BaseModel):
    """Один шаг конвейера в трассировке."""

    id: str
    name: str
    started_at: datetime
    duration_ms: int | None
    payload: dict[str, Any]


class TraceSummaryResponse(BaseModel):
    """Краткая сводка трассировки (для списка)."""

    id: str
    conversation_id: str | None
    message_id: str | None
    ts: datetime
    total_ms: int | None
    status: str
    span_count: int


class TraceListResponse(BaseModel):
    """Список трассировок."""

    traces: list[TraceSummaryResponse]
    total: int


class TraceDetailResponse(BaseModel):
    """Полная трассировка со всеми span'ами."""

    id: str
    conversation_id: str | None
    message_id: str | None
    ts: datetime
    total_ms: int | None
    status: str
    spans: list[SpanResponse]
