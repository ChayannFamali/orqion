"""Тесты API аналитики: 403 без view_analytics, срезы, пустые данные.

Проверки:
- роль без view_analytics → 403
- роль с view_analytics (manager, admin) → 200
- срезы по дням, моделям, пользователям
- роль подтягивается через JOIN (текущая, не на момент события)
- пустые данные → 0/пустые списки
- date range по умолчанию — последние 7 дней
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import (
    Model,
    Provider,
    Role,
    Team,
    UsageDaily,
    User,
)
from app.policy.presets import BUILTIN_ROLES
from app.usage.constants import NIL_ID
from fastapi import FastAPI


async def _login_with_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
    policy: dict[str, Any] | None = None,
    email: str | None = None,
    team_id: str | None = None,
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
            team_id=team_id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _create_team(app_fixture: FastAPI, name: str = "Engineering") -> str:
    """Create a team and return its ID."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        team = Team(
            workspace_id=workspace_id,
            name=name,
        )
        session.add(team)
        await session.flush()
        await session.commit()
        return team.id


async def _create_user_with_team(
    app_fixture: FastAPI,
    role_id: str,
    email: str,
    team_id: str | None = None,
) -> str:
    """Create a user with a given role and team, return user ID."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        user = User(
            workspace_id=workspace_id,
            email=email,
            password_hash=hash_password("password-123"),
            role_id=role_id,
            team_id=team_id,
        )
        session.add(user)
        await session.flush()
        await session.commit()
        return user.id


async def _seed_usage_daily(
    app_fixture: FastAPI,
    user_id: str | None = None,
    model_id: str | None = None,
    date_str: str = "2026-08-08",
    requests: int = 10,
    tokens_in: int = 1000,
    tokens_out: int = 500,
    cost: float = 0.005,
    errors: int = 1,
) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        daily = UsageDaily(
            workspace_id=workspace_id,
            date=date_str,
            user_id=user_id or NIL_ID,
            model_id=model_id or NIL_ID,
            requests=requests,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            errors=errors,
            avg_latency_ms=150,
        )
        session.add(daily)
        await session.commit()


async def _seed_provider_and_model(app_fixture: FastAPI) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()
        model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias="local/test-model",
            upstream_name="test-model",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(model)
        await session.commit()
        return model.id


@pytest.mark.asyncio
async def test_developer_without_view_analytics_gets_403(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """developer не имеет view_analytics → 403."""
    await _login_with_role(api_client, app_fixture, "developer")

    response = await api_client.get("/api/analytics")
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "analytics_forbidden"


@pytest.mark.asyncio
async def test_support_without_view_analytics_gets_403(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """support не имеет view_analytics → 403."""
    await _login_with_role(api_client, app_fixture, "support")

    response = await api_client.get("/api/analytics")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_manager_with_view_analytics_gets_200(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """manager имеет view_analytics → 200."""
    await _login_with_role(api_client, app_fixture, "manager")

    response = await api_client.get("/api/analytics?start=2026-08-01&end=2026-08-09")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_with_wildcard_gets_200(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """admin (capabilities=["*"]) → 200."""
    await _login_with_role(api_client, app_fixture, "admin")

    response = await api_client.get("/api/analytics?start=2026-08-01&end=2026-08-09")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_analytics_summary_with_data(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Summary содержит корректные totals."""
    team_id = await _create_team(app_fixture)
    user_id = await _login_with_role(api_client, app_fixture, "manager", team_id=team_id)
    model_id = await _seed_provider_and_model(app_fixture)
    await _seed_usage_daily(
        app_fixture,
        user_id=user_id,
        model_id=model_id,
        date_str="2026-08-08",
        requests=10,
        tokens_in=1000,
        tokens_out=500,
        cost=0.005,
        errors=1,
    )
    await _seed_usage_daily(
        app_fixture,
        user_id=user_id,
        model_id=model_id,
        date_str="2026-08-09",
        requests=5,
        tokens_in=500,
        tokens_out=250,
        cost=0.003,
        errors=0,
    )

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    assert response.status_code == 200
    body = response.json()
    summary = body["summary"]
    assert summary["total_requests"] == 15
    assert summary["total_tokens_in"] == 1500
    assert summary["total_tokens_out"] == 750
    assert abs(summary["total_cost"] - 0.008) < 0.0001
    assert summary["total_errors"] == 1


@pytest.mark.asyncio
async def test_analytics_by_day(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """by_day содержит разбивку по дням."""
    team_id = await _create_team(app_fixture)
    user_id = await _login_with_role(api_client, app_fixture, "manager", team_id=team_id)
    await _seed_provider_and_model(app_fixture)
    await _seed_usage_daily(app_fixture, user_id=user_id, date_str="2026-08-08", requests=10)
    await _seed_usage_daily(app_fixture, user_id=user_id, date_str="2026-08-09", requests=5)

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    body = response.json()
    by_day = body["by_day"]
    assert len(by_day) == 2
    dates = [d["date"] for d in by_day]
    assert "2026-08-08" in dates
    assert "2026-08-09" in dates


@pytest.mark.asyncio
async def test_analytics_by_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """by_model содержит разбивку по моделям."""
    team_id = await _create_team(app_fixture)
    user_id = await _login_with_role(api_client, app_fixture, "manager", team_id=team_id)
    model_id = await _seed_provider_and_model(app_fixture)
    await _seed_usage_daily(
        app_fixture,
        user_id=user_id,
        model_id=model_id,
        date_str="2026-08-08",
        requests=10,
    )

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    body = response.json()
    by_model = body["by_model"]
    assert len(by_model) == 1
    assert by_model[0]["model_id"] == model_id
    assert by_model[0]["model_alias"] == "local/test-model"
    assert by_model[0]["requests"] == 10


@pytest.mark.asyncio
async def test_analytics_by_user_with_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """by_user содержит текущую роль пользователя (JOIN user → role)."""
    team_id = await _create_team(app_fixture)
    user_id = await _login_with_role(api_client, app_fixture, "manager", team_id=team_id)
    await _seed_provider_and_model(app_fixture)
    await _seed_usage_daily(
        app_fixture,
        user_id=user_id,
        date_str="2026-08-08",
        requests=10,
    )

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    body = response.json()
    by_user = body["by_user"]
    assert len(by_user) == 1
    assert by_user[0]["user_id"] == user_id
    assert by_user[0]["user_email"] == "manager@orqion.local"
    assert by_user[0]["role_name"] == "manager"


@pytest.mark.asyncio
async def test_analytics_empty_data(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Пустые данные → summary с нулями, пустые списки."""
    await _login_with_role(api_client, app_fixture, "manager")

    response = await api_client.get("/api/analytics?start=2026-08-01&end=2026-08-09")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_requests"] == 0
    assert body["summary"]["total_cost"] == 0.0
    assert body["by_day"] == []
    assert body["by_model"] == []
    assert body["by_user"] == []


@pytest.mark.asyncio
async def test_analytics_unauthenticated(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Без логина → 401."""
    response = await api_client.get("/api/analytics")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_analytics_multiple_users_separate_rows(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """2 пользователя → 2 строки в by_user."""
    team_id = await _create_team(app_fixture)
    manager_id = await _login_with_role(
        api_client, app_fixture, "manager", email="m1@orqion.local", team_id=team_id
    )
    model_id = await _seed_provider_and_model(app_fixture)
    await _seed_usage_daily(
        app_fixture,
        user_id=manager_id,
        model_id=model_id,
        date_str="2026-08-08",
        requests=10,
    )

    # Второй пользователь в том же workspace и team
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        from sqlalchemy import select as sel

        existing_role = (
            (await session.execute(sel(Role).where(Role.workspace_id == workspace_id)))
            .scalars()
            .first()
        )
        assert existing_role is not None

        user2 = User(
            workspace_id=workspace_id,
            email="dev1@orqion.local",
            password_hash=hash_password("p"),
            role_id=existing_role.id,
            team_id=team_id,
        )
        session.add(user2)
        await session.flush()
        await session.commit()
        user2_id = user2.id

    await _seed_usage_daily(
        app_fixture,
        user_id=user2_id,
        model_id=model_id,
        date_str="2026-08-08",
        requests=5,
    )

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    body = response.json()
    by_user = body["by_user"]
    assert len(by_user) == 2


# === T-402a: Team-scoped analytics for manager ===


@pytest.mark.asyncio
async def test_manager_sees_only_own_team(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Manager sees only users in their team, not other teams."""
    team_a = await _create_team(app_fixture, "Engineering")
    team_b = await _create_team(app_fixture, "Sales")

    model_id = await _seed_provider_and_model(app_fixture)
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id

    # Create manager in team_a
    manager_id = await _login_with_role(
        api_client, app_fixture, "manager", email="mgr@orqion.local", team_id=team_a
    )
    await _seed_usage_daily(app_fixture, user_id=manager_id, model_id=model_id, requests=10)

    # Create a developer role first
    async with factory() as session:
        dev_role = Role(
            workspace_id=workspace_id,
            name="developer",
            is_builtin=True,
            policy=BUILTIN_ROLES["developer"].model_dump(),
        )
        session.add(dev_role)
        await session.commit()
        dev_role_id = dev_role.id

    # Create developer in team_a (same team as manager)
    dev_a_id = await _create_user_with_team(
        app_fixture, dev_role_id, "dev_a@orqion.local", team_id=team_a
    )
    await _seed_usage_daily(app_fixture, user_id=dev_a_id, model_id=model_id, requests=20)

    # Create developer in team_b (different team)
    dev_b_id = await _create_user_with_team(
        app_fixture, dev_role_id, "dev_b@orqion.local", team_id=team_b
    )
    await _seed_usage_daily(app_fixture, user_id=dev_b_id, model_id=model_id, requests=30)

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    assert response.status_code == 200
    body = response.json()

    # Manager should see only team_a members (manager + dev_a = 2)
    by_user = body["by_user"]
    assert len(by_user) == 2
    user_ids = {u["user_id"] for u in by_user}
    assert manager_id in user_ids
    assert dev_a_id in user_ids
    assert dev_b_id not in user_ids

    # Summary should reflect only team_a data (10 + 20 = 30 requests)
    assert body["summary"]["total_requests"] == 30


@pytest.mark.asyncio
async def test_admin_sees_all_teams(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin sees all users across all teams."""
    team_a = await _create_team(app_fixture, "Engineering")
    team_b = await _create_team(app_fixture, "Sales")
    model_id = await _seed_provider_and_model(app_fixture)
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id

    # Login as admin
    await _login_with_role(api_client, app_fixture, "admin")

    # Create users in both teams
    async with factory() as session:
        dev_role = Role(
            workspace_id=workspace_id,
            name="developer",
            is_builtin=True,
            policy=BUILTIN_ROLES["developer"].model_dump(),
        )
        session.add(dev_role)
        await session.commit()
        dev_role_id = dev_role.id

    dev_a_id = await _create_user_with_team(
        app_fixture, dev_role_id, "dev_a@orqion.local", team_id=team_a
    )
    dev_b_id = await _create_user_with_team(
        app_fixture, dev_role_id, "dev_b@orqion.local", team_id=team_b
    )

    await _seed_usage_daily(app_fixture, user_id=dev_a_id, model_id=model_id, requests=10)
    await _seed_usage_daily(app_fixture, user_id=dev_b_id, model_id=model_id, requests=20)

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    assert response.status_code == 200
    body = response.json()

    # Admin should see both teams
    by_user = body["by_user"]
    user_ids = {u["user_id"] for u in by_user}
    assert dev_a_id in user_ids
    assert dev_b_id in user_ids
    assert body["summary"]["total_requests"] == 30


@pytest.mark.asyncio
async def test_manager_without_team_sees_empty(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Manager without team_id sees empty analytics (0 results)."""
    model_id = await _seed_provider_and_model(app_fixture)
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id

    # Login as manager WITHOUT team_id (NULL)
    manager_id = await _login_with_role(
        api_client, app_fixture, "manager", email="mgr_noteam@orqion.local"
    )
    await _seed_usage_daily(app_fixture, user_id=manager_id, model_id=model_id, requests=10)

    # Create another user with no team
    async with factory() as session:
        dev_role = Role(
            workspace_id=workspace_id,
            name="developer",
            is_builtin=True,
            policy=BUILTIN_ROLES["developer"].model_dump(),
        )
        session.add(dev_role)
        await session.commit()
        dev_role_id = dev_role.id

    dev_id = await _create_user_with_team(
        app_fixture, dev_role_id, "dev@orqion.local", team_id=None
    )

    await _seed_usage_daily(app_fixture, user_id=dev_id, model_id=model_id, requests=20)

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    assert response.status_code == 200
    body = response.json()

    # Manager with NULL team_id sees nothing (no team to filter by)
    assert body["summary"]["total_requests"] == 0
    assert body["by_user"] == []
    assert body["by_model"] == []
    assert body["by_day"] == []


@pytest.mark.asyncio
async def test_analytics_by_user_includes_team_name(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """by_user response includes team_name from JOIN User → Team."""
    team_a = await _create_team(app_fixture, "Engineering")
    model_id = await _seed_provider_and_model(app_fixture)

    manager_id = await _login_with_role(
        api_client, app_fixture, "manager", email="mgr@orqion.local", team_id=team_a
    )
    await _seed_usage_daily(app_fixture, user_id=manager_id, model_id=model_id, requests=10)

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    body = response.json()
    by_user = body["by_user"]
    assert len(by_user) == 1
    assert by_user[0]["team_name"] == "Engineering"


@pytest.mark.asyncio
async def test_admin_sees_team_name_in_by_user(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin sees team_name for all users, including NULL team_id users."""
    team_a = await _create_team(app_fixture, "Engineering")
    model_id = await _seed_provider_and_model(app_fixture)
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id

    await _login_with_role(api_client, app_fixture, "admin")

    async with factory() as session:
        dev_role = Role(
            workspace_id=workspace_id,
            name="developer",
            is_builtin=True,
            policy=BUILTIN_ROLES["developer"].model_dump(),
        )
        session.add(dev_role)
        await session.commit()
        dev_role_id = dev_role.id

    dev_a_id = await _create_user_with_team(
        app_fixture, dev_role_id, "dev_a@orqion.local", team_id=team_a
    )
    dev_b_id = await _create_user_with_team(
        app_fixture, dev_role_id, "dev_b@orqion.local", team_id=None
    )

    await _seed_usage_daily(app_fixture, user_id=dev_a_id, model_id=model_id, requests=10)
    await _seed_usage_daily(app_fixture, user_id=dev_b_id, model_id=model_id, requests=20)

    response = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-09")
    body = response.json()
    by_user = {u["user_id"]: u for u in body["by_user"]}

    assert by_user[dev_a_id]["team_name"] == "Engineering"
    assert by_user[dev_b_id]["team_name"] is None


@pytest.mark.asyncio
async def test_export_csv_matches_analytics_data(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """T-434: CSV export содержит те же данные, что GET /api/analytics.

    Паттерн — T-428: сравнение экспорта с JSON-эндпоинтом на той же выборке.
    """
    team_id = await _create_team(app_fixture)
    user_id = await _login_with_role(api_client, app_fixture, "manager", team_id=team_id)
    model_id = await _seed_provider_and_model(app_fixture)
    await _seed_usage_daily(
        app_fixture,
        user_id=user_id,
        model_id=model_id,
        date_str="2026-08-08",
        requests=10,
        tokens_in=1000,
        tokens_out=500,
        cost=0.005,
        errors=1,
    )

    # GET /api/analytics — JSON
    json_resp = await api_client.get("/api/analytics?start=2026-08-08&end=2026-08-08")
    assert json_resp.status_code == 200
    json_body = json_resp.json()

    # GET /api/analytics/export — CSV
    csv_resp = await api_client.get("/api/analytics/export?start=2026-08-08&end=2026-08-08")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in csv_resp.headers["content-disposition"]

    # Парсим CSV
    import csv as csv_mod
    import io as io_mod

    reader = csv_mod.DictReader(io_mod.StringIO(csv_resp.text))
    rows = list(reader)

    # Summary row
    summary_rows = [r for r in rows if r["section"] == "summary"]
    assert len(summary_rows) == 1
    assert int(summary_rows[0]["requests"]) == json_body["summary"]["total_requests"]
    assert int(summary_rows[0]["tokens_in"]) == json_body["summary"]["total_tokens_in"]
    assert int(summary_rows[0]["tokens_out"]) == json_body["summary"]["total_tokens_out"]
    assert abs(float(summary_rows[0]["cost"]) - json_body["summary"]["total_cost"]) < 0.0001

    # Daily rows
    daily_rows = [r for r in rows if r["section"] == "daily"]
    assert len(daily_rows) == len(json_body["by_day"])
    assert daily_rows[0]["date"] == json_body["by_day"][0]["date"]
    assert int(daily_rows[0]["requests"]) == json_body["by_day"][0]["requests"]

    # Model rows
    model_rows = [r for r in rows if r["section"] == "model"]
    assert len(model_rows) == len(json_body["by_model"])
    assert int(model_rows[0]["requests"]) == json_body["by_model"][0]["requests"]

    # User rows
    user_rows = [r for r in rows if r["section"] == "user"]
    assert len(user_rows) == len(json_body["by_user"])
    assert int(user_rows[0]["requests"]) == json_body["by_user"][0]["requests"]


@pytest.mark.asyncio
async def test_export_csv_forbidden_without_view_analytics(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """T-434: export без view_analytics → 403 (не 404, T-120)."""
    await _login_with_role(api_client, app_fixture, "developer")

    response = await api_client.get("/api/analytics/export")
    assert response.status_code == 403
    assert response.json()["error"] == "analytics_forbidden"
