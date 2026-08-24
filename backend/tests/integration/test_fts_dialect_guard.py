"""BUG-017: FTS5-поиск по диалогам существует только в SQLite.

Регресс-тесты runtime-гейтов (миграция 0024 покрыта в
test_postgres_migration.py). PostgreSQL эмулируется патчем
`fts5_available` — реального демона локально нет; поведение
недоступного инструмента — честный отказ/пропуск, не падение.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, User
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from fastapi import FastAPI
from sqlalchemy import text as sa_text


async def _login_as_admin(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="admin@orqion.local",
            password_hash=hash_password("admin-password-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


async def _seed_provider_and_model(app_fixture: FastAPI) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", app_fixture.state.secret_key),
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias="local/model",
            upstream_name="model",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(model)
        await session.commit()


def _patch_fts_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Эмуляция не-SQLite диалекта: все import-точки резолвят атрибут при вызове."""
    monkeypatch.setattr("app.utils.fts5.fts5_available", lambda session: False)


@pytest.mark.asyncio
async def test_search_returns_501_when_fts_unavailable(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Честный 501 с причиной вместо молчаливого пустого результата (§7.3)."""
    await _login_as_admin(api_client, app_fixture)
    _patch_fts_unavailable(monkeypatch)

    response = await api_client.get("/api/conversations/search", params={"q": "hello"})
    assert response.status_code == 501
    body = response.json()
    assert body["error"] == "feature_not_supported"
    assert body["hint"] == "Полнотекстовый поиск по диалогам доступен только для SQLite"


@pytest.mark.asyncio
async def test_chat_save_skips_fts_dual_write_when_unavailable(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dual-write пропускается: чат сохраняется, в fts_messages ничего не пишется."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        yield {"type": "token", "v": "answer"}

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)
    _patch_fts_unavailable(monkeypatch)

    response = await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hello"}], "stream": True},
    )
    assert response.status_code == 200

    # Диалог и сообщения сохранены, но в FTS-таблицу ничего не записано
    convs = await api_client.get("/api/conversations")
    assert convs.json()["total"] == 1
    conv_id = convs.json()["conversations"][0]["id"]

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        count = (
            await session.execute(
                sa_text("SELECT COUNT(*) FROM fts_messages WHERE conversation_id = :cid"),
                {"cid": conv_id},
            )
        ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_delete_conversation_ok_when_fts_unavailable(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Удаление диалога не падает, когда таблицы нет (не-SQLite профиль)."""
    await _login_as_admin(api_client, app_fixture)

    created = await api_client.post("/api/conversations", json={"title": "to-delete"})
    assert created.status_code == 201
    conv_id = created.json()["id"]

    _patch_fts_unavailable(monkeypatch)

    response = await api_client.delete(f"/api/conversations/{conv_id}")
    assert response.status_code == 204

    convs = await api_client.get("/api/conversations")
    assert convs.json()["total"] == 0
