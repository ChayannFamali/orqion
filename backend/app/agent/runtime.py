"""Среда исполнения агентного модуля (Т-502).

Ленивый импорт LangGraph по паттерну ленивых зависимостей проекта
(локальный реранкер Т-444, кластеризация Т-505): без дополнения
``orqion[agent]`` модуль импортируется, ``is_agent_available()`` честно
сообщает о недоступности, а любая попытка построить граф получает
``ImportError`` с подсказкой установки — эндпоинт превращает это в
ответ 200 с ``available=false`` и причиной (решение: честная
деградация, не падение и не спрятанный раздел).

Телеметрия: транзитивно через ``langchain-core`` устанавливается клиент
``langsmith`` — он ВЫКЛЮЧЕН по умолчанию и активируется только
переменными окружения ``LANGSMITH_*``, которые проект не выставляет
(согласовано в ревью Т-502, 2026-09-03).
"""

from __future__ import annotations

from types import ModuleType


def is_agent_available() -> bool:
    """True, если langgraph установлен (дополнение orqion[agent])."""
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True


def import_langgraph() -> ModuleType:
    """Ленивый импорт langgraph с подсказкой об установке дополнения."""
    try:
        import langgraph
    except ImportError as e:
        raise ImportError(
            "langgraph не установлен. Установите orqion[agent]: pip install orqion[agent]"
        ) from e
    return langgraph
