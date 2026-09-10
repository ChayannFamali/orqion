"""Чтение значений служебных настроек исполняющим кодом.

Реестр (``registry.py``) описывает ключи; этот модуль отвечает на вопрос
«какое значение действует сейчас для этой рабочей области». Разделение
нужно, чтобы описание ключей не зависело от слоя БД.

Порядок резолва:

1. запись в ``workspace_settings`` — если есть и проходит валидацию спеки;
2. иначе дефолт из env-конфига (или из модели значения).

Откат к дефолту при **невалидной записи в БД** — осознанное решение, а не
тихая деградация. Через API такое значение попасть не может (запись
валидируется спекой), остаётся только правка базы руками. Отказ здесь
означал бы, что испорченная строка настройки блокирует выдачу сессий, то
есть доступ в продукт всем, включая администратора, который мог бы её
поправить. Откат в env-дефолт — отказ в безопасную сторону: это значение
действовало до первой записи, и для числовых ключей оно заведомо не шире
испорченного. Факт фиксируется предупреждением в журнале.
"""

from __future__ import annotations

import logging

from pydantic import JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import WorkspaceSetting
from app.settings.registry import coerce_value, default_value, get_spec

logger = logging.getLogger(__name__)


async def read_setting(
    session: AsyncSession,
    workspace_id: str,
    key: str,
    *,
    app_settings: Settings | None = None,
) -> JsonValue:
    """Действующее значение ключа: запись в БД или дефолт.

    ``app_settings`` — откуда брать env-дефолт. Передавать нужно там, где
    настройки уже разрешены (маршруты, выдача сессий): создание нового
    экземпляра ``Settings`` прочитало бы окружение заново и разошлось бы с
    тем, что видит вызывающий код.
    """
    spec = get_spec(key)
    if spec is None:
        # Программная ошибка: имя ключа пишется в коде, а не приходит извне.
        raise LookupError(f"Ключ служебной настройки не зарегистрирован: {key!r}")

    settings = app_settings if app_settings is not None else Settings()
    fallback = default_value(spec, settings)

    row = await session.get(WorkspaceSetting, (workspace_id, key))
    if row is None:
        return fallback
    try:
        return coerce_value(spec, row.value)
    except ValidationError:
        logger.warning(
            "Значение служебной настройки %r не соответствует её описанию, "
            "используется значение по умолчанию",
            key,
        )
        return fallback


async def read_setting_as[T](
    session: AsyncSession,
    workspace_id: str,
    key: str,
    expected: type[T],
    *,
    app_settings: Settings | None = None,
) -> T:
    """То же, что ``read_setting``, но с проверкой типа на месте вызова.

    Проверка нужна потому, что описание ключа и его потребитель живут в
    разных модулях: расходиться они должны отказом в тесте, а не значением
    неожиданного типа в исполняющем коде.
    """
    value = await read_setting(session, workspace_id, key, app_settings=app_settings)
    if type(value) is not expected:
        raise TypeError(
            f"Настройка {key!r} описана значением типа {type(value).__name__}, "
            f"а потребитель ожидает {expected.__name__}"
        )
    return value
