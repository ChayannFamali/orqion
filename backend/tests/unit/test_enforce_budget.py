"""Тесты enforce_budget: месячный лимит токенов и стоимости.

Проверки:
- None budget → пропускает
- tokens_month: не превышен → пропускает
- tokens_month: превышен → BudgetExceeded
- cost_month: превышен → BudgetExceeded
- pending_tokens учитывается в проекции
- usage_daily как источник (не usage_event)
- токены других пользователей/воркспейсов не учитываются
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.db.models import Model, Provider, Role, UsageDaily, User, Workspace
from app.errors import BudgetExceeded
from app.policy.enforce import enforce_budget
from app.policy.models import Budget, Policy
from sqlalchemy.ext.asyncio import AsyncSession


def _make_policy(budget: Budget | None = None) -> Policy:
    return Policy(models=["*"], budget=budget)


async def _seed_world(
    session: AsyncSession,
    ws_id: str = "ws-1",
    user_id: str = "user-1",
    model_id: str = "model-1",
) -> None:
    """Создаёт workspace, role, user, provider, model для FK-ограничений."""
    from app.auth.passwords import hash_password

    ws = Workspace(id=ws_id, name=ws_id)
    session.add(ws)
    await session.flush()

    role = Role(
        workspace_id=ws_id,
        name="test-role",
        is_builtin=False,
        policy={"models": ["*"]},
    )
    session.add(role)
    await session.flush()

    user = User(
        id=user_id,
        workspace_id=ws_id,
        email=f"{user_id}@test.local",
        password_hash=hash_password("x"),
        role_id=role.id,
        is_active=True,
    )
    session.add(user)
    await session.flush()

    provider = Provider(
        workspace_id=ws_id,
        kind="openai",
        base_url="http://stub:1234/v1",
        enabled=True,
        capabilities={},
    )
    session.add(provider)
    await session.flush()

    model = Model(
        id=model_id,
        workspace_id=ws_id,
        provider_id=provider.id,
        alias="test-model",
        upstream_name="test-model",
        locality="local",
        enabled=True,
    )
    session.add(model)
    await session.flush()


async def _seed_usage(
    session: AsyncSession,
    ws_id: str,
    user_id: str,
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    cost: float = 0.0,
) -> None:
    today = datetime.now(tz=UTC).date().isoformat()
    session.add(
        UsageDaily(
            workspace_id=ws_id,
            date=today,
            user_id=user_id,
            model_id=model_id,
            requests=10,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            errors=0,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_budget_none_passes(db_session: AsyncSession) -> None:
    """budget=None → пропускает без запроса к БД."""
    policy = _make_policy(budget=None)
    await enforce_budget(db_session, policy, "user-1", "ws-1")


@pytest.mark.asyncio
async def test_budget_tokens_month_under_limit(db_session: AsyncSession) -> None:
    """tokens_month не превышен → пропускает."""
    policy = _make_policy(budget=Budget(tokens_month=1_000_000))
    await enforce_budget(db_session, policy, "user-1", "ws-1", pending_tokens=500)


@pytest.mark.asyncio
async def test_budget_tokens_month_exceeded(db_session: AsyncSession) -> None:
    """tokens_month превышен с учётом pending → BudgetExceeded."""
    await _seed_world(db_session)
    await _seed_usage(db_session, "ws-1", "user-1", "model-1", 800_000, 200_000)

    policy = _make_policy(budget=Budget(tokens_month=1_000_000))
    with pytest.raises(BudgetExceeded) as exc_info:
        await enforce_budget(db_session, policy, "user-1", "ws-1", pending_tokens=10_000)
    assert exc_info.value.status_code == 429
    constraint = exc_info.value.constraint
    assert constraint is not None
    assert constraint["type"] == "tokens_month"
    assert constraint["limit"] == 1_000_000
    assert constraint["used"] == 1_000_000
    assert constraint["pending"] == 10_000


@pytest.mark.asyncio
async def test_budget_cost_month_exceeded(db_session: AsyncSession) -> None:
    """cost_month превышен с учётом pending → BudgetExceeded."""
    await _seed_world(db_session)
    await _seed_usage(db_session, "ws-1", "user-1", "model-1", 0, 0, cost=8.0)

    policy = _make_policy(budget=Budget(cost_month=10))
    with pytest.raises(BudgetExceeded) as exc_info:
        await enforce_budget(db_session, policy, "user-1", "ws-1", pending_cost=3.0)
    assert exc_info.value.status_code == 429
    constraint = exc_info.value.constraint
    assert constraint is not None
    assert constraint["type"] == "cost_month"
    assert constraint["limit"] == 10


@pytest.mark.asyncio
async def test_budget_tokens_month_exactly_at_limit(db_session: AsyncSession) -> None:
    """tokens_month ровно на лимите + pending=0 → пропускает (не >)."""
    await _seed_world(db_session)
    await _seed_usage(db_session, "ws-1", "user-1", "model-1", 900_000, 100_000)

    policy = _make_policy(budget=Budget(tokens_month=1_000_000))
    await enforce_budget(db_session, policy, "user-1", "ws-1", pending_tokens=0)


@pytest.mark.asyncio
async def test_budget_ignores_other_users(db_session: AsyncSession) -> None:
    """Токены другого пользователя не учитываются в лимите."""
    await _seed_world(db_session, user_id="other-user")
    await _seed_usage(db_session, "ws-1", "other-user", "model-1", 900_000, 100_000)

    policy = _make_policy(budget=Budget(tokens_month=1_000_000))
    await enforce_budget(db_session, policy, "user-1", "ws-1", pending_tokens=100_000)


@pytest.mark.asyncio
async def test_budget_ignores_other_workspaces(db_session: AsyncSession) -> None:
    """Токены другого workspace не учитываются."""
    await _seed_world(db_session, ws_id="other-ws")
    await _seed_usage(db_session, "other-ws", "user-1", "model-1", 900_000, 100_000)

    policy = _make_policy(budget=Budget(tokens_month=1_000_000))
    await enforce_budget(db_session, policy, "user-1", "ws-1", pending_tokens=100_000)
