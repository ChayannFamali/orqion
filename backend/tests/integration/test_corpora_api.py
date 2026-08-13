"""Тест corpora API: список, создание, access control, валидация, duplicate name."""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Corpus, Role, User
from fastapi import FastAPI


async def _login_as_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
    email_suffix: str = "",
) -> str:
    """Логинит пользователя с заданной ролью. Возвращает user_id."""
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=ws_id,
            name=role_name,
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=ws_id,
            email=f"corpora-{role_name}{email_suffix}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_corpora_empty(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/corpora → пустой список для нового workspace."""
    await _login_as_role(api_client, app_fixture, "architect")

    resp = await api_client.get("/api/corpora")
    assert resp.status_code == 200
    assert resp.json()["corpora"] == []


@pytest.mark.asyncio
async def test_list_corpora_returns_created(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/corpora → список включает созданный корпус."""
    await _login_as_role(api_client, app_fixture, "architect")

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "test-corpus", "data_class": "К0"},
    )
    assert resp.status_code == 201

    resp = await api_client.get("/api/corpora")
    assert resp.status_code == 200
    corpora = resp.json()["corpora"]
    assert len(corpora) >= 1
    assert any(c["name"] == "test-corpus" for c in corpora)


@pytest.mark.asyncio
async def test_list_corpora_non_architect_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/corpora → 404 для developer (нет manage_corpora)."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/corpora")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_corpora_admin_allowed(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/corpora → 200 для admin (через *)."""
    await _login_as_role(api_client, app_fixture, "admin")

    resp = await api_client.get("/api/corpora")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_corpus(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/corpora → создаёт корпус с data_class."""
    await _login_as_role(api_client, app_fixture, "architect")

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "public", "data_class": "К0"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "public"
    assert data["data_class"] == "К0"
    assert data["id"]


@pytest.mark.asyncio
async def test_create_corpus_no_data_class(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/corpora → data_class=None по умолчанию."""
    await _login_as_role(api_client, app_fixture, "architect")

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "no-class-corpus"},
    )
    assert resp.status_code == 201
    assert resp.json()["data_class"] is None


@pytest.mark.asyncio
async def test_create_corpus_with_pinned_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/corpora → pinned_model_id сохраняется."""
    await _login_as_role(api_client, app_fixture, "architect")

    # Создаём модель через providers API (admin only, но architect не может)
    # Вместо этого вставляем напрямую
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    model_id = None
    async with factory() as session:
        from app.db.models import Model, Provider

        provider = Provider(
            workspace_id=ws_id,
            kind="openai",
            base_url="https://api.openai.com/v1",
            api_key_enc=None,
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = Model(
            workspace_id=ws_id,
            provider_id=provider.id,
            alias="test-model",
            upstream_name="gpt-4",
            locality="external",
            enabled=True,
        )
        session.add(model)
        await session.flush()
        model_id = model.id
        await session.commit()

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "pinned-corpus", "data_class": "К2", "pinned_model_id": model_id},
    )
    assert resp.status_code == 201
    assert resp.json()["pinned_model_id"] == model_id


@pytest.mark.asyncio
async def test_create_corpus_duplicate_name_400(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST с дублем имени → 400, не 500."""
    await _login_as_role(api_client, app_fixture, "architect")

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "dup-corpus", "data_class": "К0"},
    )
    assert resp.status_code == 201

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "dup-corpus", "data_class": "К1"},
    )
    assert resp.status_code == 400
    assert resp.json()["reason"]


@pytest.mark.asyncio
async def test_create_corpus_invalid_data_class(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST с невалидным data_class → 422 (Pydantic validation)."""
    await _login_as_role(api_client, app_fixture, "architect")

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "bad-class", "data_class": "K2"},  # латинская K — невалидно
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_corpus_invalid_data_class_value(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST с data_class не из К0–К3 → 422."""
    await _login_as_role(api_client, app_fixture, "architect")

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "bad-class-2", "data_class": "К5"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_corpus_empty_name_422(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST с пустым именем → 422."""
    await _login_as_role(api_client, app_fixture, "architect")

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "", "data_class": "К0"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_corpus_non_architect_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/corpora → 404 для developer."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "forbidden", "data_class": "К0"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_corpus_support_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/corpora → 404 для support."""
    await _login_as_role(api_client, app_fixture, "support")

    resp = await api_client.post(
        "/api/corpora",
        json={"name": "forbidden-2", "data_class": "К0"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_corpora_workspace_isolated(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Корпуса из другого workspace не видны."""
    await _login_as_role(api_client, app_fixture, "architect")

    # Создаём корпус в текущем workspace
    resp = await api_client.post(
        "/api/corpora",
        json={"name": "visible-corpus", "data_class": "К0"},
    )
    assert resp.status_code == 201

    # Создаём корпус в ДРУГОМ workspace напрямую
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from app.db.models import Workspace

        other_ws = Workspace(name="other-ws")
        session.add(other_ws)
        await session.flush()

        other_corpus = Corpus(
            workspace_id=other_ws.id,
            name="other-ws-corpus",
            data_class="К3",
        )
        session.add(other_corpus)
        await session.commit()

    # Список — только свой corpus
    resp = await api_client.get("/api/corpora")
    assert resp.status_code == 200
    corpora = resp.json()["corpora"]
    names = [c["name"] for c in corpora]
    assert "visible-corpus" in names
    assert "other-ws-corpus" not in names
