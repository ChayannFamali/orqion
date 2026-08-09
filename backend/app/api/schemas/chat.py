"""Схемы запроса и событий для POST /api/chat."""

from __future__ import annotations

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    messages: list[ChatMessage]
    model_alias: str | None = None
    max_tokens: int | None = None
    temperature: float = 0.7
    stream: bool = True
    corpus_data_class: str | None = None
    corpus_name: str | None = None
    task_type: str | None = None
