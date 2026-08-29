"""Т-507: библиотека сохранённых промптов — API.

GET/POST /api/prompt-templates, PUT/DELETE /api/prompt-templates/{id}.

Доступ — способность ``custom_prompts`` (без права все эндпоинты → 404).
Личные шаблоны: видны и изменяются только владельцем. Лимиты — настройки
приложения; превышение — 422. Аудит не пишется (личное содержимое).
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import PromptTemplate, Role, User
from fastapi import FastAPI
from sqlalchemy import select


async def _login_as_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
    email_suffix: str = "",
) -> None:
    """Создаёт пользователя роли (если ещё нет) и ставит его сессию в куки.

    Повторный вызов с теми же параметрами не создаёт дубль — пользователь
    уже существует, создаётся только новая сессия (переключение обратно
    к первому пользователю в тесте изоляции).
    """
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    email = f"pt-{role_name}{email_suffix}@orqion.local"
    async with factory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            role = Role(
                workspace_id=ws_id,
                name=f"{role_name}{email_suffix}",
                is_builtin=True,
                policy=BUILTIN_ROLES[role_name].model_dump(),
            )
            session.add(role)
            await session.flush()
            user = User(
                workspace_id=ws_id,
                email=email,
                password_hash=hash_password("pass-123"),
                role_id=role.id,
            )
            session.add(user)
            await session.flush()
        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)


# ---------------------------------------------------------------------------
# Доступ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_requires_auth(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.get("/api/prompt-templates")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_all_endpoints_404_without_capability(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Роль support без способности custom_prompts — все эндпоинты 404."""
    await _login_as_role(api_client, app_fixture, "support")

    assert (await api_client.get("/api/prompt-templates")).status_code == 404
    assert (
        await api_client.post("/api/prompt-templates", json={"title": "т", "body": "текст"})
    ).status_code == 404
    assert (
        await api_client.put("/api/prompt-templates/any-id", json={"title": "т", "body": "текст"})
    ).status_code == 404
    assert (await api_client.delete("/api/prompt-templates/any-id")).status_code == 404


@pytest.mark.asyncio
async def test_admin_wildcard_has_access(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_as_role(api_client, app_fixture, "admin")

    resp = await api_client.post(
        "/api/prompt-templates", json={"title": "Шаблон", "body": "Текст шаблона"}
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_list_update_delete_cycle(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/prompt-templates")
    assert resp.status_code == 200
    assert resp.json() == {"templates": []}

    created = await api_client.post(
        "/api/prompt-templates",
        json={"title": "Код-ревью", "body": "Проведи код-ревью следующего файла:"},
    )
    assert created.status_code == 201
    template_id = created.json()["id"]
    assert created.json()["title"] == "Код-ревью"
    assert "created_at" in created.json()

    resp = await api_client.get("/api/prompt-templates")
    templates = resp.json()["templates"]
    assert len(templates) == 1
    assert templates[0]["id"] == template_id
    assert templates[0]["body"] == "Проведи код-ревью следующего файла:"

    updated = await api_client.put(
        f"/api/prompt-templates/{template_id}",
        json={"title": "Код-ревью (обновлён)", "body": "Новый текст"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Код-ревью (обновлён)"
    assert updated.json()["body"] == "Новый текст"

    deleted = await api_client.delete(f"/api/prompt-templates/{template_id}")
    assert deleted.status_code == 204

    resp = await api_client.get("/api/prompt-templates")
    assert resp.json() == {"templates": []}


@pytest.mark.asyncio
async def test_owner_is_written_on_create(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Первая версия — только личные шаблоны: владелец всегда заполнен."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.post("/api/prompt-templates", json={"title": "Т", "body": "текст"})
    assert resp.status_code == 201

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        rows = (await session.execute(select(PromptTemplate))).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id is not None


@pytest.mark.asyncio
async def test_user_isolation(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    """Шаблоны пользователя не видны другому и не изменяются им."""
    await _login_as_role(api_client, app_fixture, "developer", email_suffix="-a")
    created = await api_client.post(
        "/api/prompt-templates", json={"title": "Личный А", "body": "текст А"}
    )
    assert created.status_code == 201
    template_id = created.json()["id"]

    # Второй пользователь той же роли — список пуст
    await _login_as_role(api_client, app_fixture, "developer", email_suffix="-b")
    resp = await api_client.get("/api/prompt-templates")
    assert resp.json() == {"templates": []}

    # Попытка изменить/удалить чужой шаблон — 404
    assert (
        await api_client.put(
            f"/api/prompt-templates/{template_id}",
            json={"title": "чужой", "body": "чужой"},
        )
    ).status_code == 404
    assert (await api_client.delete(f"/api/prompt-templates/{template_id}")).status_code == 404

    # Шаблон первого пользователя цел
    await _login_as_role(api_client, app_fixture, "developer", email_suffix="-a")
    resp = await api_client.get("/api/prompt-templates")
    templates = resp.json()["templates"]
    assert len(templates) == 1
    assert templates[0]["title"] == "Личный А"


# ---------------------------------------------------------------------------
# Валидация и лимиты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rejects_empty_fields(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_as_role(api_client, app_fixture, "developer")

    for body in (
        {"title": "", "body": "текст"},
        {"title": "Т", "body": ""},
        {"body": "текст"},
        {"title": "Т"},
    ):
        resp = await api_client.post("/api/prompt-templates", json=body)
        assert resp.status_code == 422, body


@pytest.mark.asyncio
async def test_create_rejects_long_title(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.post(
        "/api/prompt-templates", json={"title": "а" * 201, "body": "текст"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_body_size_limit_from_settings(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Предел длины текста — настройка приложения; превышение — 422."""
    await _login_as_role(api_client, app_fixture, "developer")

    monkeypatch.setenv("ORQION_PROMPT_TEMPLATE_MAX_CHARS", "100")

    resp = await api_client.post("/api/prompt-templates", json={"title": "Т", "body": "а" * 100})
    assert resp.status_code == 201

    resp = await api_client.post("/api/prompt-templates", json={"title": "Т2", "body": "а" * 101})
    assert resp.status_code == 422

    # Обновление сверх предела тоже отклоняется
    template_id = (await api_client.get("/api/prompt-templates")).json()["templates"][0]["id"]
    resp = await api_client.put(
        f"/api/prompt-templates/{template_id}",
        json={"title": "Т", "body": "а" * 101},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_count_limit_from_settings(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Число шаблонов на пользователя — настройка приложения; превышение — 422."""
    await _login_as_role(api_client, app_fixture, "developer")

    monkeypatch.setenv("ORQION_PROMPT_TEMPLATES_MAX_PER_USER", "2")

    for i in range(2):
        resp = await api_client.post(
            "/api/prompt-templates", json={"title": f"Т{i}", "body": "текст"}
        )
        assert resp.status_code == 201

    resp = await api_client.post("/api/prompt-templates", json={"title": "Т3", "body": "текст"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_no_audit_written(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    """Личные шаблоны не пишутся в журнал аудита (решение дизайн-ревью)."""
    from app.db.models import AuditLog

    await _login_as_role(api_client, app_fixture, "developer")

    created = await api_client.post("/api/prompt-templates", json={"title": "Т", "body": "текст"})
    template_id = created.json()["id"]
    await api_client.put(
        f"/api/prompt-templates/{template_id}", json={"title": "Т2", "body": "текст2"}
    )
    await api_client.delete(f"/api/prompt-templates/{template_id}")

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    assert [r for r in rows if "prompt" in r.action] == []
