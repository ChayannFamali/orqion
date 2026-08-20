"""Тесты T-424: GET /api/auth/me/usage — личная видимость расхода.

Проверки:
- Аутентифицированный пользователь получает свой usage (200)
- Не требует capability view_analytics (developer → 200, не 403)
- Изоляция: user A не видит usage user B
- Admin: tokens_limit=None, cost_limit=None (unlimited)
- Только текущий месяц: данные за прошлый месяц не входят
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Role, UsageDaily, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI


async def _login_with_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
    policy: dict[str, Any] | None = None,
    email: str | None = None,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role_policy = policy or BUILTIN_ROLES[role_name].model_dump()
        role = Role(
            workspace_id=workspace_id,
            name=role_name,
            is_builtin=True,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()

        user_email = email or f"{role_name}@orqion.local"
        user = User(
            workspace_id=workspace_id,
            email=user_email,
            password_hash=hash_password("password-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_usage_daily(
    app_fixture: FastAPI,
    user_id: str,
    model_id: str = "model-1",
    date_str: str | None = None,
    requests: int = 10,
    tokens_in: int = 1000,
    tokens_out: int = 500,
    cost: float = 0.005,
) -> None:
    if date_str is None:
        date_str = datetime.now(tz=UTC).date().isoformat()

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        daily = UsageDaily(
            workspace_id=workspace_id,
            date=date_str,
            user_id=user_id,
            model_id=model_id,
            requests=requests,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            errors=0,
            avg_latency_ms=150,
        )
        session.add(daily)
        await session.commit()


@pytest.mark.asyncio
async def test_me_usage_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Аутентифицированный пользователь получает свой usage."""
    user_id = await _login_with_role(api_client, app_fixture, role_name="developer")
    await _seed_usage_daily(app_fixture, user_id=user_id, tokens_in=1000, tokens_out=500, cost=0.01)

    resp = await api_client.get("/api/auth/me/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tokens_used"] == 1500
    assert data["cost_used"] == pytest.approx(0.01, abs=1e-4)
    assert data["tokens_limit"] is not None  # developer has budget
    assert data["cost_limit"] is not None
    assert data["period"] == datetime.now(tz=UTC).strftime("%Y-%m")
    assert len(data["by_model"]) == 1
    assert data["by_model"][0]["model_id"] == "model-1"
    assert data["by_model"][0]["tokens_in"] == 1000
    assert data["by_model"][0]["tokens_out"] == 500


@pytest.mark.asyncio
async def test_me_usage_no_view_analytics_required(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Пользователь без view_analytics (developer/support) получает 200, не 403."""
    for role_name in ("developer", "support"):
        user_id = await _login_with_role(
            api_client,
            app_fixture,
            role_name=role_name,
            email=f"{role_name}-usage@orqion.local",
        )
        await _seed_usage_daily(
            app_fixture, user_id=user_id, tokens_in=100, tokens_out=50, cost=0.001
        )

        resp = await api_client.get("/api/auth/me/usage")
        assert resp.status_code == 200, f"{role_name} should get 200, got {resp.status_code}"


@pytest.mark.asyncio
async def test_me_usage_isolation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """User A не видит usage user B — изоляция по user_id."""
    # Create user A
    user_a_id = await _login_with_role(
        api_client,
        app_fixture,
        role_name="developer",
        email="user-a@orqion.local",
    )
    await _seed_usage_daily(
        app_fixture,
        user_id=user_a_id,
        tokens_in=2000,
        tokens_out=1000,
        cost=0.02,
    )

    # Create user B
    user_b_id = await _login_with_role(
        api_client,
        app_fixture,
        role_name="developer",
        email="user-b@orqion.local",
    )
    await _seed_usage_daily(
        app_fixture,
        user_id=user_b_id,
        tokens_in=500,
        tokens_out=200,
        cost=0.005,
    )

    # User B queries — should see only own usage (500+200=700), not user A's (2000+1000=3000)
    resp = await api_client.get("/api/auth/me/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tokens_used"] == 700
    assert data["cost_used"] == pytest.approx(0.005, abs=1e-4)

    # Login as user A — should see 3000, not 700
    # Re-login with user A
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        from sqlalchemy import select

        user_a = await session.execute(select(User).where(User.email == "user-a@orqion.local"))
        user_a_obj = user_a.scalar_one()
        session_id_a = await create_session(session, user_a_obj.id, workspace_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id_a)

    resp_a = await api_client.get("/api/auth/me/usage")
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["tokens_used"] == 3000
    assert data_a["cost_used"] == pytest.approx(0.02, abs=1e-4)


@pytest.mark.asyncio
async def test_me_usage_unlimited_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin: tokens_limit=None, cost_limit=None (unlimited)."""
    user_id = await _login_with_role(
        api_client,
        app_fixture,
        role_name="admin",
        email="admin-usage@orqion.local",
    )
    await _seed_usage_daily(
        app_fixture, user_id=user_id, tokens_in=10000, tokens_out=5000, cost=0.1
    )

    resp = await api_client.get("/api/auth/me/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tokens_limit"] is None
    assert data["cost_limit"] is None
    assert data["tokens_used"] == 15000
    assert data["cost_used"] == pytest.approx(0.1, abs=1e-4)


@pytest.mark.asyncio
async def test_me_usage_current_month_only(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Данные за прошлый месяц не входят в текущий usage."""
    user_id = await _login_with_role(
        api_client,
        app_fixture,
        role_name="developer",
        email="month-test@orqion.local",
    )

    now = datetime.now(tz=UTC).date()
    this_month = now.isoformat()
    last_month_end: str = (now.replace(day=1) - timedelta(days=1)).isoformat()
    last_month: str = last_month_end

    await _seed_usage_daily(
        app_fixture,
        user_id=user_id,
        date_str=this_month,
        tokens_in=1000,
        tokens_out=500,
        cost=0.01,
    )
    await _seed_usage_daily(
        app_fixture,
        user_id=user_id,
        date_str=last_month,
        tokens_in=99999,
        tokens_out=99999,
        cost=99.0,
    )

    resp = await api_client.get("/api/auth/me/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tokens_used"] == 1500  # only this month
    assert data["cost_used"] == pytest.approx(0.01, abs=1e-4)


@pytest.mark.asyncio
async def test_me_usage_no_auth(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Без аутентификации → 401."""
    resp = await api_client.get("/api/auth/me/usage")
    assert resp.status_code == 401
