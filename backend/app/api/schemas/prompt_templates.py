"""Pydantic-схемы библиотеки сохранённых промптов (Т-507)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PromptTemplateResponse(BaseModel):
    id: str
    title: str
    body: str
    created_at: datetime


class PromptTemplateListResponse(BaseModel):
    templates: list[PromptTemplateResponse]


class PromptTemplateCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)


class PromptTemplateUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
