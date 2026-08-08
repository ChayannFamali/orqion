"""enforce: применение политики к действию. Порядок — arch.md §7.1.

Каждая ветка начинается с явной проверки на None/«*» — сентинел
означает отсутствие лимита, сравнение пропускается.
Ограничение по классу данных не обходится никакой ролью (ADR-12).
"""

from __future__ import annotations

import fnmatch
from typing import Protocol

from app.errors import (
    ConfigurationError,
    ContextLimitExceeded,
    DataClassViolation,
    ModelNotAllowed,
    RateLimitExceeded,
)
from app.policy.models import WILDCARD, Policy
from app.policy.rate_limiter import RateLimiter


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


def enforce(
    policy: Policy,
    action: ChatAction,
    rate_limiter: RateLimiter | None = None,
    user_id: str | None = None,
) -> None:
    """Проверяет действие против политики. Возбуждает доменное исключение при отказе.

    Порядок проверок — arch.md §7.1:
    1. Класс данных корпуса (ADR-12, не обходится никакой ролью)
    2. Видимость модели
    3. Лимит входного контекста
    4. Лимит выходного контекста (запрошенный max_tokens, не молчаливое усечение)
    5. RPM (token bucket, если передан rate_limiter)
    6. TPM (token bucket, если передан rate_limiter)
    7. Бюджет (tokens_month — блокировано T-117; cost_month — блокировано T-113)
    """
    if rate_limiter is not None and user_id is None:
        raise ConfigurationError(
            "rate_limiter передан без user_id",
            hint="Передайте user_id вместе с rate_limiter",
        )
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

    # 4. Лимит выходного контекста — отказ, не тихая подмена (arch.md §7.3)
    if policy.max_output_tokens is not None and action.output_tokens > policy.max_output_tokens:
        raise ContextLimitExceeded(
            constraint={
                "limit": policy.max_output_tokens,
                "actual": action.output_tokens,
                "type": "output",
            },
            hint="Уменьшите ожидаемый объём ответа",
        )

    # 5. RPM — sliding window token bucket
    if policy.rpm is not None and rate_limiter is not None and user_id is not None:
        reset_in = rate_limiter.check_rpm(user_id, policy.rpm)
        if reset_in is not None:
            raise RateLimitExceeded(
                constraint={
                    "limit": policy.rpm,
                    "type": "rpm",
                    "reset_in_seconds": round(reset_in, 1),
                },
                hint=f"Попробуйте через {reset_in:.0f} секунд",
            )

    # 6. TPM — sliding window token bucket
    if policy.tpm is not None and rate_limiter is not None and user_id is not None:
        reset_in = rate_limiter.check_tpm(user_id, action.input_tokens, policy.tpm)
        if reset_in is not None:
            raise RateLimitExceeded(
                constraint={
                    "limit": policy.tpm,
                    "actual": action.input_tokens,
                    "type": "tpm",
                    "reset_in_seconds": round(reset_in, 1),
                },
                hint=f"Попробуйте через {reset_in:.0f} секунд",
            )

    # 7. Бюджет — блокировано T-117.
    # Проверка tokens_month требует суммы по usage_event за календарный месяц.
    # Таблицы usage_event нет до T-117; сравнение одного запроса с месячным
    # лимитом бессмысленно (один запрос никогда не превысит месячный бюджет).
    # cost_month — блокировано T-113 (стоимость модели появится в таблице model).
    # TODO(T-117): реализовать проверку бюджета по агрегату usage_event.
