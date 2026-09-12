"""Дефолты генерации: одно значение на все пути запроса к модели.

Ответ пользователю собирается в трёх местах — обычный чат, шаг генерации
конвейера по документам и вызов модели в агентном прогоне. Температура у
них одна: это свойство рабочей области, а не того пути, которым пришёл
вопрос.

Раньше значение было зашито в каждом из трёх мест и в контракте запроса,
поэтому «температура по умолчанию» не настраивалась вовсе, а интерфейс
всегда слал своё число явно — серверный дефолт не действовал никогда.
Теперь единственный источник дефолта — запись в реестре служебных
настроек, а до первой записи env-конфиг.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.settings.registry import DEFAULT_TEMPERATURE_KEY
from app.settings.service import read_setting_as


async def read_default_temperature(
    session: AsyncSession,
    workspace_id: str,
    *,
    app_settings: Settings | None = None,
) -> float:
    """Температура генерации по умолчанию для рабочей области.

    Значение читается на каждый запрос, а не кэшируется: смена настройки
    через интерфейс действует со следующего запроса, без перезапуска.
    Дополнительный запрос к базе на запрос чата — осознанная плата за это.
    """
    return await read_setting_as(
        session, workspace_id, DEFAULT_TEMPERATURE_KEY, float, app_settings=app_settings
    )
