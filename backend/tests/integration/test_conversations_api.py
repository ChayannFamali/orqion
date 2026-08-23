"""Интеграционные тесты API диалогов: CRUD, доступ только к своим, архивация,
мягкий сброс контекста (T-442)."""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Role, User, Workspace
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI


async def _login_user(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    email: str = "user@orqion.local",
    role_name: str = "developer",
    password: str = "user-password-123",
) -> str:
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
            password_hash=hash_password(password),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _login_second_user(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> str:
    """Второй пользователь в том же workspace."""
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from sqlalchemy import select

        ws_result = await session.execute(select(Workspace))
        ws = ws_result.scalars().first()
        assert ws is not None

        role = Role(
            workspace_id=ws.id,
            name="developer",
            is_builtin=True,
            policy=BUILTIN_ROLES["developer"].model_dump(),
        )
        session.add(role)
        await session.flush()

        password = "second-password-123"
        user = User(
            workspace_id=ws.id,
            email="second@orqion.local",
            password_hash=hash_password(password),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


@pytest.mark.asyncio
async def test_create_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture)

    response = await api_client.post("/api/conversations", json={"title": "Test chat"})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Test chat"
    assert body["archived"] is False
    assert body["message_count"] == 0
    assert body["messages"] == []


@pytest.mark.asyncio
async def test_create_conversation_without_title(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Заголовок пустой до первого сообщения."""
    await _login_user(api_client, app_fixture)

    response = await api_client.post("/api/conversations", json={})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == ""


@pytest.mark.asyncio
async def test_list_conversations(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture)

    await api_client.post("/api/conversations", json={"title": "Chat 1"})
    await api_client.post("/api/conversations", json={"title": "Chat 2"})
    await api_client.post("/api/conversations", json={"title": "Chat 3"})

    response = await api_client.get("/api/conversations")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["conversations"]) == 3


@pytest.mark.asyncio
async def test_list_conversations_archived_filter(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture)

    await api_client.post("/api/conversations", json={"title": "Active"})
    resp2 = await api_client.post("/api/conversations", json={"title": "Archived"})
    conv2_id = resp2.json()["id"]

    await api_client.patch(f"/api/conversations/{conv2_id}", json={"archived": True})

    active_resp = await api_client.get("/api/conversations?archived=false")
    assert active_resp.json()["total"] == 1
    assert active_resp.json()["conversations"][0]["title"] == "Active"

    archived_resp = await api_client.get("/api/conversations?archived=true")
    assert archived_resp.json()["total"] == 1
    assert archived_resp.json()["conversations"][0]["title"] == "Archived"


@pytest.mark.asyncio
async def test_get_conversation_empty(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture)

    create_resp = await api_client.post("/api/conversations", json={"title": "Empty"})
    conv_id = create_resp.json()["id"]

    response = await api_client.get(f"/api/conversations/{conv_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == conv_id
    assert body["messages"] == []
    assert body["message_count"] == 0


@pytest.mark.asyncio
async def test_rename_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture)

    create_resp = await api_client.post("/api/conversations", json={"title": "Old name"})
    conv_id = create_resp.json()["id"]

    response = await api_client.patch(
        f"/api/conversations/{conv_id}",
        json={"title": "New name"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "New name"


@pytest.mark.asyncio
async def test_archive_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture)

    create_resp = await api_client.post("/api/conversations", json={"title": "To archive"})
    conv_id = create_resp.json()["id"]

    response = await api_client.patch(
        f"/api/conversations/{conv_id}",
        json={"archived": True},
    )
    assert response.status_code == 200
    assert response.json()["archived"] is True


@pytest.mark.asyncio
async def test_delete_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture)

    create_resp = await api_client.post("/api/conversations", json={"title": "To delete"})
    conv_id = create_resp.json()["id"]

    response = await api_client.delete(f"/api/conversations/{conv_id}")
    assert response.status_code == 204

    get_resp = await api_client.get(f"/api/conversations/{conv_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_get_nonexistent_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture)

    response = await api_client.get("/api/conversations/nonexistent-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_access_other_users_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Пользователь не видит диалоги другого пользователя в том же workspace."""
    await _login_user(api_client, app_fixture, email="first@orqion.local")

    create_resp = await api_client.post("/api/conversations", json={"title": "User 1 chat"})
    conv_id = create_resp.json()["id"]

    # Второй пользователь логинится
    await _login_second_user(api_client, app_fixture)

    # Не видит в списке
    list_resp = await api_client.get("/api/conversations")
    assert list_resp.json()["total"] == 0

    # Не может читать напрямую
    get_resp = await api_client.get(f"/api/conversations/{conv_id}")
    assert get_resp.status_code == 404

    # Не может переименовать
    patch_resp = await api_client.patch(
        f"/api/conversations/{conv_id}",
        json={"title": "Hacked"},
    )
    assert patch_resp.status_code == 404

    # Не может удалить
    delete_resp = await api_client.delete(f"/api/conversations/{conv_id}")
    assert delete_resp.status_code == 404


@pytest.mark.asyncio
async def test_list_pagination(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture)

    for i in range(5):
        await api_client.post("/api/conversations", json={"title": f"Chat {i}"})

    resp1 = await api_client.get("/api/conversations?limit=2&offset=0")
    assert len(resp1.json()["conversations"]) == 2
    assert resp1.json()["total"] == 5

    resp2 = await api_client.get("/api/conversations?limit=2&offset=2")
    assert len(resp2.json()["conversations"]) == 2

    resp3 = await api_client.get("/api/conversations?limit=2&offset=4")
    assert len(resp3.json()["conversations"]) == 1


@pytest.mark.asyncio
async def test_unauthenticated_cannot_access(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Без логина — 401."""
    response = await api_client.get("/api/conversations")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# T-442: мягкий сброс контекста
# ---------------------------------------------------------------------------


async def _add_message(
    app_fixture: FastAPI,
    conversation_id: str,
    role: str,
    content: str,
) -> None:
    """Сообщение в диалог напрямую в БД (чат-эндпоинт не нужен)."""
    from app.db.models import Message

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        session.add(
            Message(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                role=role,
                content=content,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_reset_context_sets_marker_and_keeps_messages(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Сброс ставит маркер; сообщения, заголовок и архив не тронуты."""
    await _login_user(api_client, app_fixture)
    create_resp = await api_client.post("/api/conversations", json={"title": "Resettable"})
    conv_id = create_resp.json()["id"]
    await _add_message(app_fixture, conv_id, "user", "старое сообщение")
    await _add_message(app_fixture, conv_id, "assistant", "старый ответ")

    response = await api_client.post(f"/api/conversations/{conv_id}/reset-context")
    assert response.status_code == 200
    body = response.json()
    assert body["context_reset_at"] is not None

    # Детали: маркер виден, сообщения на месте
    detail = (await api_client.get(f"/api/conversations/{conv_id}")).json()
    assert detail["context_reset_at"] == body["context_reset_at"]
    assert detail["message_count"] == 2
    assert [m["content"] for m in detail["messages"]] == [
        "старое сообщение",
        "старый ответ",
    ]
    assert detail["title"] == "Resettable"
    assert detail["archived"] is False


@pytest.mark.asyncio
async def test_reset_context_repeat_moves_marker(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Повторный сброс сдвигает маркер вперёд (или оставляет тем же)."""
    from datetime import datetime

    await _login_user(api_client, app_fixture)
    conv_id = (await api_client.post("/api/conversations", json={})).json()["id"]

    first = (await api_client.post(f"/api/conversations/{conv_id}/reset-context")).json()[
        "context_reset_at"
    ]
    second = (await api_client.post(f"/api/conversations/{conv_id}/reset-context")).json()[
        "context_reset_at"
    ]
    assert datetime.fromisoformat(second) >= datetime.fromisoformat(first)


@pytest.mark.asyncio
async def test_reset_context_is_per_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Сброс одного диалога не затрагивает другие."""
    await _login_user(api_client, app_fixture)
    conv_a = (await api_client.post("/api/conversations", json={"title": "A"})).json()["id"]
    conv_b = (await api_client.post("/api/conversations", json={"title": "B"})).json()["id"]

    await api_client.post(f"/api/conversations/{conv_a}/reset-context")

    detail_b = (await api_client.get(f"/api/conversations/{conv_b}")).json()
    assert detail_b["context_reset_at"] is None


@pytest.mark.asyncio
async def test_reset_context_other_users_conversation_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_user(api_client, app_fixture, email="first@orqion.local")
    conv_id = (await api_client.post("/api/conversations", json={})).json()["id"]

    await _login_second_user(api_client, app_fixture)
    response = await api_client.post(f"/api/conversations/{conv_id}/reset-context")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reset_context_unauthenticated_401(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    response = await api_client.post("/api/conversations/some-id/reset-context")
    assert response.status_code == 401
