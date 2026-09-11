"""Ограничения загрузки документов: одно значение для всех путей загрузки.

Файл попадает в корпус двумя путями — загрузкой через интерфейс и импортом
git-репозитория. Оба читают действующие ограничения одним хелпером: предел
размера и список расширений — свойство рабочей области, а не того пути,
которым файл принесли.

Раньше второй путь брал значения из собственного встроенного списка, который
был шире списка из env-конфига, и предел размера у него был зашит в коде.
Расхождение не было видно ни в одном месте: один и тот же файл в одном пути
принимался, в другом — нет.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.settings.registry import ALLOWED_UPLOAD_EXTENSIONS_KEY, MAX_UPLOAD_SIZE_KEY
from app.settings.service import read_setting_as

logger = logging.getLogger(__name__)

#: Байт в мегабайте — единице настройки размера.
BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True)
class UploadLimits:
    """Действующие ограничения на один загружаемый файл."""

    max_file_size_bytes: int
    allowed_extensions: tuple[str, ...]


def parse_extensions(raw: str) -> tuple[str, ...]:
    """Список расширений через запятую → отсортированный кортеж в нижнем регистре.

    Пустая строка даёт пустой список, то есть запрещает все файлы: список
    без элементов означает ноль, а не «всё».

    Порядок фиксирован, потому что список виден пользователю — в подсказке
    при отказе и в теле ошибки.

    Запись через интерфейс проходит валидацию описания ключа, поэтому
    расширение без точки оттуда прийти не может. Env-конфиг никто не
    валидирует, и такой элемент приводится к виду с точкой с
    предупреждением в журнале: отказ здесь означал бы, что опечатка в
    переменной окружения ломает загрузку файлов целиком.
    """
    dotted: set[str] = set()
    fixed: list[str] = []
    for item in {part.strip().lower() for part in raw.split(",") if part.strip()}:
        if item.startswith("."):
            dotted.add(item)
        else:
            fixed.append(item)
            dotted.add(f".{item}")
    if fixed:
        logger.warning(
            "В списке разрешённых расширений элементы без ведущей точки: %s — учтены как %s",
            ", ".join(sorted(fixed)),
            ", ".join(f".{item}" for item in sorted(fixed)),
        )
    return tuple(sorted(dotted))


async def read_upload_limits(
    session: AsyncSession,
    workspace_id: str,
    *,
    app_settings: Settings | None = None,
) -> UploadLimits:
    """Действующие ограничения загрузки для рабочей области.

    Значения читаются на каждый вызов, а не кэшируются: смена настройки
    через интерфейс начинает действовать со следующей загрузки, без
    перезапуска. Дополнительный запрос к базе на загрузку — осознанная
    плата за это.
    """
    settings = app_settings if app_settings is not None else Settings()
    max_size_mb = await read_setting_as(
        session, workspace_id, MAX_UPLOAD_SIZE_KEY, int, app_settings=settings
    )
    raw_extensions = await read_setting_as(
        session, workspace_id, ALLOWED_UPLOAD_EXTENSIONS_KEY, str, app_settings=settings
    )
    return UploadLimits(
        max_file_size_bytes=max_size_mb * BYTES_PER_MB,
        allowed_extensions=parse_extensions(raw_extensions),
    )
