"""Схемы запроса и ответа для conversation/message эндпоинтов."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    model_id: str | None
    tokens_in: int | None
    tokens_out: int | None
    created_at: datetime
    meta: dict[str, object]


class ConversationResponse(BaseModel):
    id: str
    title: str
    archived: bool
    created_at: datetime
    message_count: int = 0


class ConversationDetailResponse(ConversationResponse):
    messages: list[MessageResponse] = []


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    archived: bool | None = None


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int


class MessageSearchResult(BaseModel):
    """Результат полнотекстового поиска по диалогам (T-436)."""

    message_id: str
    conversation_id: str
    role: str
    content: str
    score: float
