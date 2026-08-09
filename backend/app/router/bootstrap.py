"""Идемпотентный seed правил маршрутизации по умолчанию.

arch.md §7.2: правила упорядочены, первое совпадение задаёт множество,
последующие только сужают. default — последнее правило.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RoutingRule

DEFAULT_RULES: list[dict[str, object]] = [
    {
        "order": 0,
        "is_default": False,
        "is_terminal": True,
        "when_corpus_class": ["К2", "К3"],
        "when_role": None,
        "when_task": None,
        "when_model_alias": None,
        "to_models": [],
        "allow_locality": ["local"],
        "fallback_models": [],
        "reason": "конфиденциальный корпус — только локальные модели",
    },
    {
        "order": 1,
        "is_default": False,
        "is_terminal": True,
        "when_corpus_class": None,
        "when_role": "support",
        "when_task": None,
        "when_model_alias": None,
        "to_models": ["local/*"],
        "allow_locality": None,
        "fallback_models": [],
        "reason": "support — только локальные модели",
    },
    {
        "order": 2,
        "is_default": False,
        "is_terminal": False,
        "when_corpus_class": None,
        "when_role": None,
        "when_task": "code",
        "when_model_alias": None,
        "to_models": [],
        "allow_locality": None,
        "fallback_models": [],
        "reason": "код — приоритет coder-моделям (reserved for future task classification)",
    },
    {
        "order": 99,
        "is_default": True,
        "is_terminal": True,
        "when_corpus_class": None,
        "when_role": None,
        "when_task": None,
        "when_model_alias": None,
        "to_models": [],
        "allow_locality": None,
        "fallback_models": [],
        "reason": "default — все включённые модели",
    },
]


async def ensure_default_routing_rules(
    session: AsyncSession,
    workspace_id: str,
) -> None:
    """Создаёт правила маршрутизации по умолчанию, если их нет.

    Идемпотентно: если таблица пуста для workspace — вставляет DEFAULT_RULES.
    Существующие правила не перезаписываются (пользователь мог их редактировать).
    """
    result = await session.execute(
        select(RoutingRule).where(RoutingRule.workspace_id == workspace_id)
    )
    if result.scalars().first() is not None:
        return

    for rule_data in DEFAULT_RULES:
        rule = RoutingRule(
            workspace_id=workspace_id,
            **rule_data,
        )
        session.add(rule)
    await session.flush()
