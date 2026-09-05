"""Схемы запроса и ответа агентного модуля (Т-502)."""

from __future__ import annotations

from pydantic import BaseModel

from app.api.schemas.chat import ChatMessage, ChatSourceEntry, ChatUsage


class PendingConfirmation(BaseModel):
    """Запрос подтверждения деструктивного действия (пункт 9 ревью Т-502).

    Возвращается, когда модель запросила деструктивный инструмент:
    прогон остановлен до выполнения, клиент показывает пользователю
    инструмент и аргументы, решение возвращается следующим запросом
    (поле ``confirmation_decision``).
    """

    call_id: str
    tool: str
    args: dict[str, object]


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
    # Скилл — пакет конфигурации прогона (Т-508): выбор приходит на
    # запрос, а не хранится на диалоге, чтобы не столкнуться с будущим
    # ``agent_profile_id`` из Т-509 (решение 5). Несуществующий или
    # отключённый ``skill_id`` — явный отказ 400, не тихий откат к прогону
    # без скилла (решение 5, правка пользователя).
    skill_id: str | None = None
    # Цикл подтверждения деструктивного инструмента (пункт 9): клиент
    # возвращает запрос подтверждения из прошлого ответа вместе с
    # решением. ``approve`` исполняет инструмент, ``reject`` отменяет
    # действие без вызова модели.
    confirmation_decision: str | None = None
    confirmation: PendingConfirmation | None = None


class AgentStepEntry(BaseModel):
    """Шаг прогона для ленты агентного диалога."""

    index: int
    kind: str  # "model" | "tool" | "confirmation" | "skill"
    name: str | None = None
    summary: str = ""
    decision: str | None = None  # "allow" | "deny" | "approve" | "reject" | "pending"


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
    # Инструменты скилла, недоступные в этом прогоне (решение 6): честное
    # сообщение «скилл обещал N, доступно M» по паттерну усечения Т-504.
    # Пусто, если скилл не выбран или все его инструменты доступны.
    skill_tools_unavailable: list[str] = []
    # Поля ошибки — как у ответа чата (единый обработчик доменных ошибок
    # возвращает их же для исключений, не перехваченных роутом).
    code: str | None = None
    constraint: dict[str, object] | None = None
    hint: str | None = None
