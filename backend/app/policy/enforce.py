"""enforce: применение политики к действию. Порядок — arch.md §7.1.

Каждая ветка начинается с явной проверки на None/«*» — сентинел
означает отсутствие лимита, сравнение пропускается.
Ограничение по классу данных не обходится никакой ролью (ADR-12).
"""

from __future__ import annotations

import fnmatch
from typing import Protocol

from app.errors import (
    BudgetExceeded,
    ContextLimitExceeded,
    DataClassViolation,
    ModelNotAllowed,
    RateLimitExceeded,
)
from app.policy.models import WILDCARD, Policy


class ChatAction(Protocol):
    """Действие, проверяемое enforce()."""

    model_alias: str
    model_locality: str
    input_tokens: int
    output_tokens: int
    corpus_data_class: str | None
    corpus_name: str | None


def _matches(patterns: list[str], value: str) -> bool:
    """Проверяет, соответствует ли значение хотя бы одному шаблону."""
    if WILDCARD in patterns:
        return True
    return any(fnmatch.fnmatch(value, p) for p in patterns)


def enforce(policy: Policy, action: ChatAction) -> None:
    """Проверяет действие против политики. Возбуждает доменное исключение при отказе.

    Порядок проверок — arch.md §7.1:
    1. Класс данных корпуса (ADR-12, не обходится никакой ролью)
    2. Видимость модели
    3. Лимит входного контекста
    4. Бюджет, tpm, rpm
    """
    # 1. Класс данных — первая проверка, не смотрит на политику
    if action.corpus_data_class in ("К2", "К3") and action.model_locality != "local":
        raise DataClassViolation(
            constraint={
                "data_class": action.corpus_data_class,
                "model": action.model_alias,
                "locality": action.model_locality,
            },
            hint="Для этого корпуса допустимы только локальные модели",
        )

    # 2. Видимость модели
    if not _matches(policy.models, action.model_alias):
        raise ModelNotAllowed(
            constraint={
                "model": action.model_alias,
                "available": policy.models,
            },
            hint="Выберите модель из доступного списка",
        )

    # 3. Лимит входного контекста
    if policy.max_input_tokens is not None and action.input_tokens > policy.max_input_tokens:
        raise ContextLimitExceeded(
            constraint={"limit": policy.max_input_tokens, "actual": action.input_tokens},
            hint="Сократите запрос или выберите модель с большим контекстом",
        )

    # 4. Лимит выходного контекста
    if policy.max_output_tokens is not None and action.output_tokens > policy.max_output_tokens:
        raise ContextLimitExceeded(
            constraint={
                "limit": policy.max_output_tokens,
                "actual": action.output_tokens,
                "type": "output",
            },
            hint="Уменьшите ожидаемый объём ответа",
        )

    # 5. RPM
    if policy.rpm is not None and action.input_tokens > 0:
        # RPM проверяется по факту вызова, не по токенам — заглушка для T-108
        pass

    # 6. TPM
    if policy.tpm is not None and action.input_tokens > policy.tpm:
        raise RateLimitExceeded(
            constraint={"limit": policy.tpm, "actual": action.input_tokens, "type": "tpm"},
            hint="Превышен лимит токенов в минуту",
        )

    # 7. Бюджет
    if policy.budget is not None:
        tokens_month = policy.budget.get("tokens_month")
        if tokens_month is not None and action.input_tokens > tokens_month:
            raise BudgetExceeded(
                constraint={"limit": tokens_month, "actual": action.input_tokens},
                hint="Превышен месячный бюджет токенов",
            )
