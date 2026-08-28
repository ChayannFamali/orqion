"""Pydantic-схемы настроек RAG-поиска (Т-506)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RagSettingsResponse(BaseModel):
    relevance_threshold: int
    max_fragments: int


class RagSettingsUpdate(BaseModel):
    relevance_threshold: int = Field(ge=0, le=100)
    max_fragments: int = Field(ge=1, le=8)
