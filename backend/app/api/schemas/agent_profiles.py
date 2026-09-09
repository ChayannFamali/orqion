"""Т-509: схемы профилей агентов — переиспользуемых конфигураций диалога.

Профиль — это модель + скилл (решение 1): поля системного промпта и
списка инструментов в схеме НЕТ построением, а не проверкой. Их роль
выполняет скилл (Т-508), поэтому вторая сущность «промпт + инструменты»
не заводится — иначе у одного поведения появилось бы два источника
правды.

``extra="forbid"`` по образцу скиллов: неподписанное поле
(``system_prompt``, ``tools``, ``auto_approve``) даёт 422, а не молчаливое
игнорирование.

Два списка (решение 3, паттерн Т-508):

- ``AgentProfileResponse`` — админский каталог, полные поля, включая
  отключённые профили (иначе отключённый нельзя включить обратно);
- ``AgentProfileAvailableResponse`` — список для выбора при старте
  диалога: всем аутентифицированным, только включённые и только поля
  выбора.

``AgentProfileConversationEntry`` (решение 5) — drill-down по диалогам
профиля: только метаданные и расход. Содержимого переписки в схеме нет
вовсе, поэтому отдать его невозможно даже при наличии права. Заголовок
диалога — первые 80 символов первого сообщения, то есть содержимое, —
присутствует только в записях о собственных диалогах (``title`` пустой у
чужих).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

NAME_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 512


class AgentProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    description: str = Field(default="", max_length=DESCRIPTION_MAX_LENGTH)
    model_id: str
    skill_id: str | None = None
    enabled: bool = True


class AgentProfileUpdate(BaseModel):
    """Полная замена полей профиля (по образцу скиллов, Т-508).

    Имя переименовывать можно: оно отображаемое, ссылкой служит
    ``agent_profile_id`` на диалоге, поэтому переименование не меняет ни
    записей аудита, ни привязки существующих диалогов.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    description: str = Field(default="", max_length=DESCRIPTION_MAX_LENGTH)
    model_id: str
    skill_id: str | None = None
    enabled: bool = True


class AgentProfileResponse(BaseModel):
    """Админский каталог: полные поля, включая отключённые профили."""

    id: str
    name: str
    description: str
    model_id: str
    # Алиас модели и имя скилла — разрешённые на сервере, чтобы карточка
    # профиля не требовала второго запроса за справочниками.
    model_alias: str
    skill_id: str | None
    skill_name: str | None
    enabled: bool
    created_at: datetime


class AgentProfileListResponse(BaseModel):
    profiles: list[AgentProfileResponse]


class AgentProfileAvailableResponse(BaseModel):
    """Список для выбора при старте диалога: только нужное для выбора."""

    id: str
    name: str
    description: str


class AgentProfileAvailableListResponse(BaseModel):
    profiles: list[AgentProfileAvailableResponse]


class AgentProfileDeleteResponse(BaseModel):
    deleted: bool


class AgentProfileConversationEntry(BaseModel):
    """Диалог профиля в drill-down: метаданные и расход, без переписки.

    Расход — агрегат существующего ``usage_event.conversation_id``
    (решение 5): новая таблица накопления не заводится.
    """

    id: str
    user_id: str
    # Содержимое переписки не отдаётся никогда, даже с правом
    # ``manage_agents`` (решение 5). Заголовок — производное от первого
    # сообщения, поэтому заполнен только для собственных диалогов.
    title: str | None
    archived: bool
    created_at: datetime
    last_activity_at: datetime
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0


class AgentProfileConversationListResponse(BaseModel):
    conversations: list[AgentProfileConversationEntry]
    total: int
    # Чьи диалоги в выдаче: ``own`` — только свои (без права),
    # ``workspace`` — все диалоги рабочей области (с ``manage_agents``).
    # Третьего варианта нет (решение 5), поле делает фактический охват
    # видимым для проверяющего.
    scope: str
