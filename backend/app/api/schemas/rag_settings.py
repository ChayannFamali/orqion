"""Pydantic-схемы настроек RAG-поиска (Т-506, число групп графа — Т-505)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RagSettingsResponse(BaseModel):
    relevance_threshold: int
    max_fragments: int
    cluster_count: int


class RagSettingsUpdate(BaseModel):
    relevance_threshold: int = Field(ge=0, le=100)
    max_fragments: int = Field(ge=1, le=8)
    cluster_count: int = Field(ge=2, le=20)
