"""Схемы запроса, ответа и событий для POST /api/chat."""

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
    # T-439: мульти-корпусный RAG. corpus_name и corpus_names взаимно
    # исключают друг друга (оба заданы → 400). Пустой список = обычный чат.
    corpus_names: list[str] | None = None
    task_type: str | None = None


class ChatUsage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0


class ChatSourceEntry(BaseModel):
    chunk_id: str
    document_id: str
    structural_path: str
    score: float
    original_rank: int
    # T-439: атрибуция корпуса — из какого корпуса фрагмент.
    corpus_id: str | None = None
    corpus_name: str | None = None


class ChatResponse(BaseModel):
    """Ответ POST /api/chat (non-streaming: RAG + plain complete)."""

    type: str
    content: str = ""
    conversation_id: str | None = None
    model: str | None = None
    usage: ChatUsage | None = None
    rag_degraded: bool = False
    rag_errors: list[str] = []
    sources: list[ChatSourceEntry] = []
    code: str | None = None
    reason: str | None = None
    constraint: dict[str, object] | None = None
    hint: str | None = None
