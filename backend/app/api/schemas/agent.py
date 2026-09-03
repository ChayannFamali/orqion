"""Схемы запроса и ответа агентного модуля (Т-502)."""

from __future__ import annotations

from pydantic import BaseModel

from app.api.schemas.chat import ChatMessage, ChatSourceEntry, ChatUsage


class AgentChatRequest(BaseModel):
    """Запрос агентного прогона.

    Модель обязательна: агентный цикл работает только с моделями, у
    которых администратор включил флаг ``supports_tools`` (решение 3).
    ``messages`` — буфер диалога, как в обычном чате (клиент управляет
    историей); последнее сообщение — вопрос пользователя.
    """

    conversation_id: str | None = None
    messages: list[ChatMessage]
    model_alias: str
    corpus_names: list[str] | None = None
    max_tokens: int | None = None


class AgentStepEntry(BaseModel):
    """Шаг прогона для ленты агентного диалога."""

    index: int
    kind: str  # "model" | "tool"
    name: str | None = None
    summary: str = ""
    decision: str | None = None  # для инструментов: "allow" | "deny"


class PendingConfirmation(BaseModel):
    """Запрос подтверждения деструктивного действия (пункт 9).

    Механизм заложен в Т-502; в этой задаче деструктивных инструментов
    нет, поле всегда ``null``. Реальное использование проверяется в
    Т-503/Т-508.
    """

    call_id: str
    tool: str
    args: dict[str, object]


class AgentChatResponse(BaseModel):
    """Ответ POST /api/agent/chat.

    Честная деградация (паттерн Т-444/Т-505): без дополнения
    ``orqion[agent]`` — 200 с ``available=false`` и явной причиной.
    """

    available: bool = True
    reason: str | None = None
    type: str = "complete"
    content: str = ""
    conversation_id: str | None = None
    model: str | None = None
    usage: ChatUsage | None = None
    steps: list[AgentStepEntry] = []
    sources: list[ChatSourceEntry] = []
    trace_id: str | None = None
    pending_confirmation: PendingConfirmation | None = None
    # Поля ошибки — как у ответа чата (единый обработчик доменных ошибок
    # возвращает их же для исключений, не перехваченных роутом).
    code: str | None = None
    constraint: dict[str, object] | None = None
    hint: str | None = None
