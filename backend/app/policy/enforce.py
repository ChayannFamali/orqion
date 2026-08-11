"""enforce: применение политики к действию. Порядок — arch.md §7.1.

Каждая ветка начинается с явной проверки на None/«*» — сентинел
означает отсутствие лимита, сравнение пропускается.
Ограничение по классу данных не обходится никакой ролью (ADR-12).
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Protocol

from app.errors import (
    BudgetExceeded,
    ConfigurationError,
    ContextLimitExceeded,
    DataClassViolation,
    Forbidden,
    ModelNotAllowed,
    RateLimitExceeded,
)
from app.policy.models import WILDCARD, Policy
from app.policy.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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

    # 1.5. Видимость корпуса — проверка до поиска (S-12, ADR-12)
    if action.corpus_name is not None and not _matches(policy.corpora, action.corpus_name):
        raise Forbidden(
            constraint={
                "corpus": action.corpus_name,
                "allowed": policy.corpora,
            },
            hint="Корпус не разрешён политикой роли",
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

    # 7. Бюджет — асинхронная проверка, вызывается отдельно (enforce_budget).
    # T-108: tokens_month и cost_month проверяются через usage_daily.


async def enforce_budget(
    session: AsyncSession,
    policy: Policy,
    user_id: str,
    workspace_id: str,
    pending_tokens: int = 0,
    pending_cost: float = 0.0,
) -> None:
    """Проверяет месячный бюджет по агрегату usage_daily (T-108).

    Шаг 7 в arch.md §7.1. Вызывается после enforce(), т.к. требует БД-запроса.

    policy.budget = {"tokens_month": int, "cost_month": int} или None.
    Источник — usage_daily за текущий календарный месяц по user_id.
    pending_tokens/pending_cost — затраты текущего запроса (ещё не записанные).
    """
    if policy.budget is None:
        return

    tokens_month_limit = policy.budget.get("tokens_month")
    cost_month_limit = policy.budget.get("cost_month")

    if tokens_month_limit is None and cost_month_limit is None:
        return

    from datetime import UTC, datetime

    from sqlalchemy import func, select

    from app.db.models import UsageDaily

    today = datetime.now(tz=UTC).date()
    month_start = today.replace(day=1).isoformat()

    result = await session.execute(
        select(
            func.coalesce(func.sum(UsageDaily.tokens_in + UsageDaily.tokens_out), 0).label(
                "total_tokens"
            ),
            func.coalesce(func.sum(UsageDaily.cost), 0.0).label("total_cost"),
        ).where(
            UsageDaily.workspace_id == workspace_id,
            UsageDaily.user_id == user_id,
            UsageDaily.date >= month_start,
        )
    )
    row = result.one()
    used_tokens = int(row.total_tokens)
    used_cost = float(row.total_cost)

    if tokens_month_limit is not None:
        projected_tokens = used_tokens + pending_tokens
        if projected_tokens > tokens_month_limit:
            raise BudgetExceeded(
                constraint={
                    "limit": tokens_month_limit,
                    "used": used_tokens,
                    "pending": pending_tokens,
                    "type": "tokens_month",
                },
                hint="Месячный лимит токенов исчерпан",
            )

    if cost_month_limit is not None:
        projected_cost = used_cost + pending_cost
        if projected_cost > cost_month_limit:
            raise BudgetExceeded(
                constraint={
                    "limit": cost_month_limit,
                    "used": round(used_cost, 4),
                    "pending": round(pending_cost, 4),
                    "type": "cost_month",
                },
                hint="Месячный лимит расходов исчерпан",
            )


async def enforce_all(
    policy: Policy,
    action: ChatAction,
    session: AsyncSession,
    user_id: str,
    workspace_id: str,
    rate_limiter: RateLimiter | None = None,
    model_cost_in: float | None = None,
    model_cost_out: float | None = None,
) -> None:
    """Фасад: все проверки политики в одном вызове (arch.md §7.1, шаги 1-7).

    Синхронные проверки 1-6 (enforce) + асинхронная проверка 7 (enforce_budget).
    Вызывающий код передаёт model_cost_in/out — только он знает выбранную модель.
    pending_tokens = input + output, pending_cost = input*cost_in + output*cost_out.
    """
    enforce(policy, action, rate_limiter=rate_limiter, user_id=user_id)

    pending_tokens = action.input_tokens + action.output_tokens
    pending_cost = 0.0
    if model_cost_in is not None:
        pending_cost += action.input_tokens * model_cost_in
    if model_cost_out is not None:
        pending_cost += action.output_tokens * model_cost_out
    await enforce_budget(
        session,
        policy,
        user_id=user_id,
        workspace_id=workspace_id,
        pending_tokens=pending_tokens,
        pending_cost=pending_cost,
    )
