"""Pydantic-схемы для corpora API (T-312)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# arch.md §8.5 — кириллическая "К" (U+041A), не латинская "K"
DataClass = Literal["К0", "К1", "К2", "К3"]


class CorpusCreate(BaseModel):
    """Создание корпуса. data_class валидируется (К0–К3 или None)."""

    name: str = Field(min_length=1, max_length=255)
    data_class: DataClass | None = None
    pinned_model_id: str | None = None


class CorpusUpdate(BaseModel):
    """Изменение корпуса (T-401). Только data_class; pinned_model_id — T-402."""

    data_class: DataClass | None = None


class CorpusResponse(BaseModel):
    """Корпус в ответе API."""

    id: str
    name: str
    data_class: str | None
    pinned_model_id: str | None
    active_index_version_id: str | None


class CorpusListResponse(BaseModel):
    """Список корпусов."""

    corpora: list[CorpusResponse]
