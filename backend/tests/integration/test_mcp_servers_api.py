"""Тесты API реестра серверов протокола (Т-503).

Админский CRUD: секрет шифруется механизмом ключей провайдеров и не
возвращается; имя сервера — неймспейс инструментов (формат без точки,
уникальность в рабочей области); доступ только со способностью
``manage_mcp_servers`` (без права — 404, по образцу провайдеров).
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import decrypt_api_key
from app.db.models import McpServer, Role, User, Workspace
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

        user = User(
            workspace_id=ws.id,
            email="admin@orqion.local",
            password_hash=hash_password("admin-password-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


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
            email=f"mcp-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


async def _create_server(
    api_client: httpx.AsyncClient,
    name: str = "wiki",
    url: str = "http://localhost:9100/mcp",
    api_key: str | None = None,
) -> httpx.Response:
    payload: dict[str, object] = {"name": name, "url": url, "enabled": True}
    if api_key is not None:
        payload["api_key"] = api_key
    return await api_client.post("/api/mcp-servers", json=payload)


@pytest.mark.asyncio
async def test_create_server_key_encrypted_not_returned(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Секрет шифруется при записи и не возвращается в ответе."""
    await _login_as_admin(api_client, app_fixture)

    response = await _create_server(api_client, api_key="mcp-secret-123")
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "wiki"
    assert body["url"] == "http://localhost:9100/mcp"
    assert body["has_api_key"] is True
    assert "api_key" not in body
    assert "api_key_enc" not in body

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(select(McpServer))
        server = result.scalar_one()
        assert server.api_key_enc is not None
        assert server.api_key_enc != "mcp-secret-123"
        secret_key = app_fixture.state.secret_key
        assert decrypt_api_key(server.api_key_enc, secret_key) == "mcp-secret-123"


@pytest.mark.asyncio
async def test_create_server_without_key(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Сервер без секрета (локальный) — api_key_enc=None."""
    await _login_as_admin(api_client, app_fixture)

    response = await _create_server(api_client)
    assert response.status_code == 201
    assert response.json()["has_api_key"] is False

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        server = (await session.execute(select(McpServer))).scalar_one()
        assert server.api_key_enc is None


@pytest.mark.parametrize(
    "bad_name",
    ["With-Upper", "with.dot", "9start", "", "имя", "has space"],
)
@pytest.mark.asyncio
async def test_create_server_invalid_name_rejected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    bad_name: str,
) -> None:
    """Имя — неймспейс инструментов: строчная латиница/цифры/дефис/подчёркивание, без точки."""
    await _login_as_admin(api_client, app_fixture)

    response = await _create_server(api_client, name=bad_name)
    assert response.status_code == 422


@pytest.mark.parametrize("bad_url", ["ftp://server.local/mcp", "not-a-url", "http://"])
@pytest.mark.asyncio
async def test_create_server_bad_url_rejected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    bad_url: str,
) -> None:
    """Транспорт только HTTP к явному адресу (решение 1)."""
    await _login_as_admin(api_client, app_fixture)

    response = await _create_server(api_client, url=bad_url)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_server_duplicate_name_conflict(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Имя уникально в рабочей области — второй сервер с тем же именем отклонён."""
    await _login_as_admin(api_client, app_fixture)

    first = await _create_server(api_client, name="wiki")
    assert first.status_code == 201

    second = await _create_server(api_client, name="wiki", url="http://other.local/mcp")
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_list_servers(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Список серверов без секретов, отсортирован по имени."""
    await _login_as_admin(api_client, app_fixture)

    assert (await _create_server(api_client, name="zeta")).status_code == 201
    assert (await _create_server(api_client, name="alpha", api_key="s")).status_code == 201

    response = await api_client.get("/api/mcp-servers")
    assert response.status_code == 200
    servers = response.json()["servers"]
    assert [s["name"] for s in servers] == ["alpha", "zeta"]
    for s in servers:
        assert "api_key" not in s
        assert "api_key_enc" not in s
    assert servers[0]["has_api_key"] is True
    assert servers[1]["has_api_key"] is False


@pytest.mark.asyncio
async def test_update_server_fields(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH обновляет адрес и флаг; имя не меняется (неймспейс инструментов)."""
    await _login_as_admin(api_client, app_fixture)

    created = await _create_server(api_client)
    server_id = created.json()["id"]

    response = await api_client.patch(
        f"/api/mcp-servers/{server_id}",
        json={"url": "https://new-host.local/mcp", "enabled": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "https://new-host.local/mcp"
    assert body["enabled"] is False
    assert body["name"] == "wiki"

    # Имя в схеме обновления отсутствует — смена неймспейса запрещена построением.
    rename = await api_client.patch(f"/api/mcp-servers/{server_id}", json={"name": "other"})
    assert rename.status_code == 200
    assert rename.json()["name"] == "wiki"


@pytest.mark.asyncio
async def test_update_server_replaces_key(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH api_key перешифровывает секрет."""
    await _login_as_admin(api_client, app_fixture)

    created = await _create_server(api_client, api_key="old-secret")
    server_id = created.json()["id"]

    response = await api_client.patch(
        f"/api/mcp-servers/{server_id}", json={"api_key": "new-secret"}
    )
    assert response.status_code == 200
    assert response.json()["has_api_key"] is True

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        server = (
            await session.execute(select(McpServer).where(McpServer.id == server_id))
        ).scalar_one()
        secret_key = app_fixture.state.secret_key
        assert decrypt_api_key(server.api_key_enc or "", secret_key) == "new-secret"


@pytest.mark.asyncio
async def test_delete_server(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """DELETE убирает сервер из реестра; повторное удаление — 404."""
    await _login_as_admin(api_client, app_fixture)

    created = await _create_server(api_client)
    server_id = created.json()["id"]

    deleted = await api_client.delete(f"/api/mcp-servers/{server_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    listing = await api_client.get("/api/mcp-servers")
    assert listing.json()["servers"] == []

    again = await api_client.delete(f"/api/mcp-servers/{server_id}")
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_servers_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Без способности manage_mcp_servers все операции реестра — 404."""
    await _login_as_admin(api_client, app_fixture)
    created = await _create_server(api_client)
    server_id = created.json()["id"]

    await _login_as_role(api_client, app_fixture, "developer")

    assert (await api_client.get("/api/mcp-servers")).status_code == 404
    assert (await _create_server(api_client, name="intruder")).status_code == 404
    assert (
        await api_client.patch(f"/api/mcp-servers/{server_id}", json={"enabled": False})
    ).status_code == 404
    assert (await api_client.delete(f"/api/mcp-servers/{server_id}")).status_code == 404
