"""Схемы для управления версиями индекса (T-314)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class IndexVersionResponse(BaseModel):
    """Версия индекса с прогрессом."""

    id: str
    corpus_id: str
    embedding_model: str
    chunker: str
    chunker_version: str
    status: str
    stats: dict[str, object] | None = None
    created_at: datetime


class IndexVersionListResponse(BaseModel):
    """Список версий индекса корпуса."""

    versions: list[IndexVersionResponse]
    total: int


class ActivateResponse(BaseModel):
    """Результат активации версии индекса."""

    active_version_id: str
    previous_version_id: str | None = None
    warning: str | None = None


class RollbackResponse(BaseModel):
    """Результат отката версии индекса."""

    active_version_id: str
    retired_version_id: str | None = None


class CleanupResponse(BaseModel):
    """Результат очистки retired-версий."""

    deleted_count: int


class BuildResponse(BaseModel):
    """Ответ на запуск сборки индекса (202 Accepted)."""

    index_version_id: str
    status: str = "building"
