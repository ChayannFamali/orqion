"""Pydantic-схемы личных настроек пользователя (раздел «Профиль»).

Форма ответа — контракт рендера раздела: по каждому зарегистрированному
ключу отдаётся не только значение, но и всё, что нужно, чтобы нарисовать
поле, не зная самого ключа. Новая личная настройка поэтому не требует
правок во фронтенде — тот же принцип, что во вкладке служебных настроек.

Два отличия от ``WorkspaceSettingResponse``:

- ``editable`` всегда ``true``. Право на личную настройку — владение
  строкой, а не способность роли: маршрут отдаёт только строки владельца,
  значит менять их ему можно. Поле сохранено, чтобы оба списка рисовал один
  компонент интерфейса и чтобы у клиента не появлялось двух форм ответа для
  одинаково выглядящих полей;
- ``enum_labels`` — подписи значений перечисления на языке интерфейса.
  Машинное значение (``enter``) в списке выбора не показывается; у ключей
  без перечисления поле равно ``null``.

Предел длины ключа и метка источника значения заимствованы из схем
служебных настроек: колонка ``key`` в обеих таблицах одна и та же
(String(128)), а источник значения означает одно и то же.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.api.schemas.workspace_settings import KEY_MAX_LENGTH, ValueSource
from app.settings.registry import UiValueType


class UserPreferenceResponse(BaseModel):
    """Один ключ реестра с резолвнутым значением и описанием поля."""

    key: str
    title: str
    description: str
    category: str
    type: UiValueType
    enum_values: list[str] | None = None
    enum_labels: dict[str, str] | None = None
    value: JsonValue = None
    source: ValueSource
    editable: bool
    min: float | None = None
    max: float | None = None


class UserPreferenceListResponse(BaseModel):
    preferences: list[UserPreferenceResponse]


class UserPreferenceUpdate(BaseModel):
    """Запись одного ключа за вызов.

    Батча нет намеренно: при отказе по одному из ключей непонятно, какое
    именно поле его вызвало. Причина та же, что у служебных настроек.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=KEY_MAX_LENGTH)
    value: JsonValue = None
