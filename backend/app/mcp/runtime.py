"""Среда исполнения клиента протокола (Т-503).

Ленивый импорт по паттерну ленивых зависимостей проекта (локальный
реранкер Т-444, кластеризация Т-505, агентный цикл Т-502): без
дополнения ``orqion[mcp]`` модуль импортируется,
``is_mcp_available()`` честно сообщает о недоступности, а любая
попытка открыть сессию получает ``ImportError`` с подсказкой
установки.

Телеметрия: в отличие от ``langgraph``/``langsmith``, клиент
протокола телеметрии не тянет — проверено ``pip freeze`` на чистом
venv при согласовании зависимости (2026-09-03); в дереве ветки 1.х
нет телеметрических клиентов.
"""

from __future__ import annotations

from types import ModuleType


def is_mcp_available() -> bool:
    """True, если mcp установлен (дополнение orqion[mcp])."""
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


def import_mcp() -> ModuleType:
    """Ленивый импорт mcp с подсказкой об установке дополнения."""
    try:
        import mcp
    except ImportError as e:
        raise ImportError(
            "mcp не установлен. Установите orqion[mcp]: pip install orqion[mcp]"
        ) from e
    # Без установленного дополнения (джоба типов с одним [dev]) имя
    # типизировано как Any — возврат через объявленную переменную, чтобы
    # не нарушать ``warn_return_any``.
    module: ModuleType = mcp
    return module
