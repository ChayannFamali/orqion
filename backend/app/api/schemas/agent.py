"""Схемы запроса и ответа агентного модуля (Т-502)."""

from __future__ import annotations

from pydantic import BaseModel, model_validator

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

    Профиль агента (Т-509, решение 2) — второй способ задать
    конфигурацию прогона: он фиксирует модель и скилл, поэтому при
    выбранном профиле поля ``model_alias`` и ``skill_id`` в запросе
    запрещены (переопределение — явный отказ, а не молчаливое
    игнорирование). Диалог, уже созданный от профиля, продолжает его:
    профиль берётся с диалога, а не из запроса.
    """

    conversation_id: str | None = None
    messages: list[ChatMessage]
    model_alias: str | None = None
    agent_profile_id: str | None = None
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

    @model_validator(mode="after")
    def _require_model_or_profile(self) -> AgentChatRequest:
        """Модель нужна, только если конфигурацию не задаёт профиль/диалог.

        Проверка на схеме, а не в маршруте: отказ приходит обычным 422 с
        указанием поля, до создания трассировки и обращения к политике.
        Продолжение существующего диалога (``conversation_id``) модель в
        запросе не обязано нести — для профильного диалога её источник
        профиль, для ad-hoc диалога клиент её передаёт сам.
        """
        if (
            self.model_alias is None
            and self.agent_profile_id is None
            and self.conversation_id is None
        ):
            raise ValueError(
                "Укажите model_alias или agent_profile_id: "
                "конфигурация прогона должна быть задана явно"
            )
        return self


class AgentStepEntry(BaseModel):
    """Шаг прогона для ленты агентного диалога."""

    index: int
    kind: str  # "model" | "tool" | "confirmation" | "skill" | "stop"
    name: str | None = None
    summary: str = ""
    decision: str | None = None  # "allow" | "deny" | "approve" | "reject" | "pending"


class AgentChatResponse(BaseModel):
    """Ответ POST /api/agent/chat.

    Честная деградация (паттерн Т-444/Т-505): без дополнения
    ``orqion[agent]`` — 200 с ``available=false`` и явной причиной.

    ``type``: ``complete`` — прогон дошёл до финального ответа;
    ``stopped`` — прогон остановлен по запросу пользователя между шагами
    (Т-509, решение 7); ``error`` — исчерпан лимит прогона.
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
