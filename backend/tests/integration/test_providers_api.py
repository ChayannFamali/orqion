"""Тест providers API: создание, список, обновление, ключ не возвращается."""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import decrypt_api_key
from app.db.models import Role, User, Workspace
from fastapi import FastAPI
from sqlalchemy import select


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Создаёт admin-пользователя и логинится через cookie."""
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        ws = Workspace(name="test")
        session.add(ws)
        await session.flush()

        role = Role(
            workspace_id=ws.id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()

        password = "admin-password-123"
        user = User(
            workspace_id=ws.id,
            email="admin@orqion.local",
            password_hash=hash_password(password),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


@pytest.mark.asyncio
async def test_create_provider_key_encrypted_not_returned(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """API-ключ шифруется при записи и не возвращается в ответе."""
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.post(
        "/api/providers",
        json={
            "kind": "external",
            "base_url": "http://localhost:1234/v1",
            "api_key": "sk-secret-key-123",
            "enabled": True,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "external"
    assert body["base_url"] == "http://localhost:1234"
    assert "api_key" not in body
    assert "api_key_enc" not in body

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from app.db.models import Provider

        result = await session.execute(select(Provider))
        provider = result.scalar_one()
        assert provider.api_key_enc is not None
        assert provider.api_key_enc != "sk-secret-key-123"
        secret_key = app_fixture.state.secret_key
        assert decrypt_api_key(provider.api_key_enc, secret_key) == "sk-secret-key-123"


@pytest.mark.asyncio
async def test_create_provider_without_key(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Провайдер без API-ключа (локальный) — api_key_enc=None."""
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.post(
        "/api/providers",
        json={
            "kind": "ollama",
            "base_url": "http://localhost:11434",
            "enabled": True,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "ollama"
    assert "api_key" not in body


@pytest.mark.asyncio
async def test_base_url_normalized_on_save(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """BUG-011: base_url сохраняется в канонической форме (create и PATCH)."""
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.post(
        "/api/providers",
        json={
            "kind": "lmstudio",
            "base_url": "http://localhost:1234/v1/",
            "enabled": True,
        },
    )
    assert response.status_code == 201
    provider_id = response.json()["id"]
    assert response.json()["base_url"] == "http://localhost:1234"

    response = await api_client.patch(
        f"/api/providers/{provider_id}",
        json={"base_url": "http://localhost:1234/v1"},
    )
    assert response.status_code == 200
    assert response.json()["base_url"] == "http://localhost:1234"


@pytest.mark.asyncio
async def test_list_providers(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_as_admin(api_client, app_fixture)

    await api_client.post(
        "/api/providers",
        json={"kind": "ollama", "base_url": "http://localhost:11434"},
    )
    await api_client.post(
        "/api/providers",
        json={"kind": "lmstudio", "base_url": "http://localhost:1234/v1"},
    )

    response = await api_client.get("/api/providers")
    assert response.status_code == 200
    body = response.json()
    assert len(body["providers"]) == 2


@pytest.mark.asyncio
async def test_update_provider_key_replaced(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """При обновлении ключа старый шифр заменяется новым."""
    await _login_as_admin(api_client, app_fixture)

    create_response = await api_client.post(
        "/api/providers",
        json={
            "kind": "external",
            "base_url": "http://localhost:1234/v1",
            "api_key": "sk-old-key",
        },
    )
    provider_id = create_response.json()["id"]

    await api_client.patch(
        f"/api/providers/{provider_id}",
        json={"api_key": "sk-new-key"},
    )

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from app.db.models import Provider

        result = await session.execute(select(Provider).where(Provider.id == provider_id))
        provider = result.scalar_one()
        secret_key = app_fixture.state.secret_key
        assert decrypt_api_key(provider.api_key_enc, secret_key) == "sk-new-key"


@pytest.mark.asyncio
async def test_update_provider_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.patch(
        "/api/providers/nonexistent",
        json={"enabled": False},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_providers_require_auth(api_client: httpx.AsyncClient) -> None:
    """GET /api/providers без cookie → 401."""
    response = await api_client.get("/api/providers")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# T-308: Access control — manage_providers capability
# ---------------------------------------------------------------------------


async def _login_as_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
) -> None:
    """Логинит пользователя с заданной ролью (non-admin)."""
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
            email=f"prov-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


@pytest.mark.asyncio
async def test_list_providers_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/providers → 404 для non-admin (manage_providers required)."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/providers")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"


@pytest.mark.asyncio
async def test_create_provider_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/providers → 404 для non-admin."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://evil.test/v1", "api_key": "stolen"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_provider_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH /api/providers/{id} → 404 для non-admin (нельзя подменить base_url)."""
    # Создаём провайдер от admin
    await _login_as_admin(api_client, app_fixture)
    create_resp = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://legit.test/v1"},
    )
    assert create_resp.status_code == 201
    provider_id = create_resp.json()["id"]

    # Пытаемся изменить от developer
    await _login_as_role(api_client, app_fixture, "developer")
    resp = await api_client.patch(
        f"/api/providers/{provider_id}",
        json={"base_url": "http://evil.test/v1"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_probe_provider_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/providers/{id}/probe → 404 для non-admin."""
    await _login_as_admin(api_client, app_fixture)
    create_resp = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://legit.test/v1"},
    )
    provider_id = create_resp.json()["id"]

    await _login_as_role(api_client, app_fixture, "developer")
    resp = await api_client.post(f"/api/providers/{provider_id}/probe")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# T-309: Model CRUD — IntegrityError handling + provider_id ignored
# ---------------------------------------------------------------------------


async def _create_provider_and_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    *,
    model_alias: str = "test-model",
) -> tuple[str, str]:
    """Создаёт провайдер и модель, возвращает (provider_id, model_id)."""
    await _login_as_admin(api_client, app_fixture)
    create_resp = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://localhost:1234/v1"},
    )
    provider_id = create_resp.json()["id"]

    model_resp = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={
            "provider_id": provider_id,
            "alias": model_alias,
            "upstream_name": "test-model",
        },
    )
    assert model_resp.status_code == 201
    return provider_id, model_resp.json()["id"]


@pytest.mark.asyncio
async def test_update_model_duplicate_alias_clean_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH model с alias, который уже занят → чистая ошибка, не 500."""
    # Создаём две модели с разными alias
    _, model1_id = await _create_provider_and_model(api_client, app_fixture, model_alias="model-a")
    await _create_provider_and_model(api_client, app_fixture, model_alias="model-b")

    # Пытаемся переименовать model-a в model-b (занят)
    resp = await api_client.patch(
        f"/api/providers/models/{model1_id}",
        json={"alias": "model-b"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["reason"]  # не пустой — чистая доменная ошибка, не 500


@pytest.mark.asyncio
async def test_create_model_provider_id_in_body_ignored(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """provider_id в теле запроса игнорируется — path param авторитетен.

    ModelCreate.schema требует provider_id, но endpoint использует path.
    Если body.provider_id отличается от path — модель всё равно создаётся
    под провайдером из path, не из body.
    """
    await _login_as_admin(api_client, app_fixture)

    # Создаём два провайдера
    resp1 = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://p1.test/v1"},
    )
    provider1_id = resp1.json()["id"]
    resp2 = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://p2.test/v1"},
    )
    provider2_id = resp2.json()["id"]

    # Создаём модель под provider1, но в body указываем provider2
    model_resp = await api_client.post(
        f"/api/providers/{provider1_id}/models",
        json={
            "provider_id": provider2_id,  # отличается от path
            "alias": "cross-provider-test",
            "upstream_name": "test",
        },
    )
    assert model_resp.status_code == 201

    # Проверяем, что модель привязана к provider1 (из path), не к provider2
    list_resp = await api_client.get("/api/providers")
    providers = list_resp.json()["providers"]
    p1 = next(p for p in providers if p["id"] == provider1_id)
    p2 = next(p for p in providers if p["id"] == provider2_id)
    assert len(p1["models"]) == 1
    assert p1["models"][0]["alias"] == "cross-provider-test"
    assert len(p2["models"]) == 0


# ---------------------------------------------------------------------------
# T-437: канонический набор kind (Pydantic-валидация на уровне API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_kind", ["lm", "openai", "LM Studio", ""])
async def test_create_provider_non_canonical_kind_rejected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    legacy_kind: str,
) -> None:
    """kind вне канонического набора {ollama, lmstudio, external} → 422."""
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.post(
        "/api/providers",
        json={"kind": legacy_kind, "base_url": "http://localhost:1234"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_provider_kind_canonical(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH kind принимает только канонические значения."""
    await _login_as_admin(api_client, app_fixture)

    create_resp = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://localhost:1234/v1"},
    )
    provider_id = create_resp.json()["id"]

    resp = await api_client.patch(
        f"/api/providers/{provider_id}",
        json={"kind": "lmstudio"},
    )
    assert resp.status_code == 200
    assert resp.json()["kind"] == "lmstudio"

    resp = await api_client.patch(
        f"/api/providers/{provider_id}",
        json={"kind": "lm"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Удаление провайдера (семантика 1: только без моделей)
# ---------------------------------------------------------------------------


async def _login_with_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
    email: str,
) -> None:
    """Локальный логин под ролью (паттерн _login_as_admin, роль параметризована)."""
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        ws = Workspace(name="test")
        session.add(ws)
        await session.flush()

        role = Role(
            workspace_id=ws.id,
            name=role_name,
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=ws.id,
            email=email,
            password_hash=hash_password("password-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


@pytest.mark.asyncio
async def test_delete_provider_without_models(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """DELETE провайдера без моделей → 200, провайдер исчезает."""
    await _login_as_admin(api_client, app_fixture)

    create_resp = await api_client.post(
        "/api/providers",
        json={"kind": "ollama", "base_url": "http://localhost:11434"},
    )
    provider_id = create_resp.json()["id"]

    resp = await api_client.delete(f"/api/providers/{provider_id}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from app.db.models import Provider

        assert await session.get(Provider, provider_id) is None


@pytest.mark.asyncio
async def test_delete_provider_with_models_conflict(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """DELETE провайдера с моделями → 409 (семантика 1, заметка к T-201)."""
    await _login_as_admin(api_client, app_fixture)

    create_resp = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://localhost:1234/v1"},
    )
    provider_id = create_resp.json()["id"]

    model_resp = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"alias": "m1", "upstream_name": "upstream-m1"},
    )
    assert model_resp.status_code == 201

    resp = await api_client.delete(f"/api/providers/{provider_id}")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "conflict"
    assert body["constraint"]["reason"] == "has_models"
    assert body["constraint"]["models_count"] == 1
    assert body["hint"]

    # Провайдер на месте
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from app.db.models import Provider

        assert await session.get(Provider, provider_id) is not None


@pytest.mark.asyncio
async def test_delete_provider_after_models_removed(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """После удаления всех моделей провайдер удаляется."""
    await _login_as_admin(api_client, app_fixture)

    create_resp = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://localhost:1234/v1"},
    )
    provider_id = create_resp.json()["id"]

    model_resp = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"alias": "m1", "upstream_name": "upstream-m1"},
    )
    model_id = model_resp.json()["id"]

    assert (await api_client.delete(f"/api/providers/{provider_id}")).status_code == 409

    del_model = await api_client.delete(f"/api/providers/models/{model_id}")
    assert del_model.status_code == 200

    resp = await api_client.delete(f"/api/providers/{provider_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_delete_provider_denied_without_capability(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Не-админ (без manage_providers) → 404."""
    await _login_as_admin(api_client, app_fixture)

    create_resp = await api_client.post(
        "/api/providers",
        json={"kind": "ollama", "base_url": "http://localhost:11434"},
    )
    provider_id = create_resp.json()["id"]

    await _login_with_role(api_client, app_fixture, "developer", "dev@orqion.local")

    resp = await api_client.delete(f"/api/providers/{provider_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_provider_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.delete("/api/providers/nonexistent-provider-id")
    assert resp.status_code == 404
