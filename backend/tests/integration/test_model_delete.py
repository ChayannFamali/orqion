"""T-443 (коммит 2): удаление модели провайдера + опциональная очистка с диска.

Приёмка:
- удаление непривязанной модели → метаданные удаляются;
- удаление модели-пина корпуса → 409, модель и привязка не тронуты;
- очистка с диска вызывает нативный API провайдера (kind-гейт DOWNLOADABLE_KINDS);
- ошибка диска отображается, но метаданные всё равно удаляются;
- исторические ссылки (message.model_id, usage_event.model_id) обнуляются.

httpx перехватывается MockTransport (прецедент test_model_download) — CI не
бьёт в реальные провайдеры.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Corpus, Model, Role, UsageEvent, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI
from sqlalchemy import select

MODEL_BODY = {"alias": "del-model", "upstream_name": "upstream-del"}


async def _login(api_client: httpx.AsyncClient, app_fixture: FastAPI, role_name: str) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name=role_name,
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email=f"t443-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)


async def _create_provider(api_client: httpx.AsyncClient, kind: str, base_url: str) -> str:
    resp = await api_client.post(
        "/api/providers", json={"kind": kind, "base_url": base_url, "enabled": True}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _create_model(api_client: httpx.AsyncClient, provider_id: str) -> str:
    resp = await api_client.post(f"/api/providers/{provider_id}/models", json=MODEL_BODY)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _pin_corpus(app_fixture: FastAPI, model_id: str) -> str:
    """Корпус с pinned_model_id = model_id. Возвращает corpus.id."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(
            workspace_id=workspace_id,
            name="t443-pinned-corpus",
            data_class="К2",
            pinned_model_id=model_id,
        )
        session.add(corpus)
        await session.commit()
        return corpus.id


async def _model_exists(app_fixture: FastAPI, model_id: str) -> bool:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(select(Model).where(Model.id == model_id))
        return result.scalar_one_or_none() is not None


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    transport = httpx.MockTransport(handler)

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)


# ---------------------------------------------------------------------------
# Базовое удаление метаданных
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_unpinned_model(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    await _login(api_client, app_fixture, "admin")
    provider_id = await _create_provider(api_client, "external", "http://api.test/v1")
    model_id = await _create_model(api_client, provider_id)

    resp = await api_client.delete(f"/api/providers/models/{model_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["disk_deleted"] is None
    assert body["disk_error"] is None
    assert await _model_exists(app_fixture, model_id) is False


@pytest.mark.asyncio
async def test_delete_pinned_model_409(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    await _login(api_client, app_fixture, "admin")
    provider_id = await _create_provider(api_client, "external", "http://api.test/v1")
    model_id = await _create_model(api_client, provider_id)
    corpus_id = await _pin_corpus(app_fixture, model_id)

    resp = await api_client.delete(f"/api/providers/models/{model_id}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "conflict"
    # Модель и привязка корпуса не тронуты
    assert await _model_exists(app_fixture, model_id) is True
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        corpus = (await session.execute(select(Corpus).where(Corpus.id == corpus_id))).scalar_one()
        assert corpus.pinned_model_id == model_id


@pytest.mark.asyncio
async def test_delete_model_nulls_history_references(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """usage_event.model_id обнуляется, запись о расходе сохраняется."""
    await _login(api_client, app_fixture, "admin")
    provider_id = await _create_provider(api_client, "external", "http://api.test/v1")
    model_id = await _create_model(api_client, provider_id)

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        session.add(UsageEvent(workspace_id=workspace_id, model_id=model_id, status="ok"))
        await session.commit()

    resp = await api_client.delete(f"/api/providers/models/{model_id}")
    assert resp.status_code == 200, resp.text

    async with factory() as session:
        events = (await session.execute(select(UsageEvent))).scalars().all()
        assert len(events) == 1
        assert events[0].model_id is None


# ---------------------------------------------------------------------------
# Гейты: права, авторизация, 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_model_non_admin_404(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture, "admin")
    provider_id = await _create_provider(api_client, "external", "http://api.test/v1")
    model_id = await _create_model(api_client, provider_id)

    await _login(api_client, app_fixture, "developer")
    resp = await api_client.delete(f"/api/providers/models/{model_id}")
    assert resp.status_code == 404
    assert await _model_exists(app_fixture, model_id) is True


@pytest.mark.asyncio
async def test_delete_model_unauthenticated_401(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.delete("/api/providers/models/some-id")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_model_not_found_404(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture, "admin")
    resp = await api_client.delete("/api/providers/models/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Очистка с диска: нативные эндпоинты + обработка ошибок
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_model_disk_ollama_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin")
    provider_id = await _create_provider(api_client, "ollama", "http://localhost:11434")
    model_id = await _create_model(api_client, provider_id)

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "method": request.method,
                "path": request.url.path,
                "body": json.loads(request.read()),
            }
        )
        return httpx.Response(200, json={})

    _patch_httpx(monkeypatch, handler)

    resp = await api_client.delete(
        f"/api/providers/models/{model_id}", params={"delete_from_disk": True}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["disk_deleted"] is True
    assert body["disk_error"] is None
    assert seen == [{"method": "DELETE", "path": "/api/delete", "body": {"model": "upstream-del"}}]
    assert await _model_exists(app_fixture, model_id) is False


@pytest.mark.asyncio
async def test_delete_model_disk_error_still_deletes_metadata(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin")
    provider_id = await _create_provider(api_client, "ollama", "http://localhost:11434")
    model_id = await _create_model(api_client, provider_id)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    _patch_httpx(monkeypatch, handler)

    resp = await api_client.delete(
        f"/api/providers/models/{model_id}", params={"delete_from_disk": True}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["disk_deleted"] is False
    assert body["disk_error"] is not None
    assert "500" in body["disk_error"]
    # Ошибка диска не блокирует удаление метаданных
    assert await _model_exists(app_fixture, model_id) is False


@pytest.mark.asyncio
async def test_delete_model_disk_lmstudio_encodes_path(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Идентификатор со слешами уходит в путь URL-кодированным."""
    await _login(api_client, app_fixture, "admin")
    provider_id = await _create_provider(api_client, "lmstudio", "http://localhost:1234")
    resp = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"alias": "hf-model", "upstream_name": "org/repo-GGUF"},
    )
    assert resp.status_code == 201
    model_id = str(resp.json()["id"])

    seen_paths: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.raw_path)
        return httpx.Response(200, json={})

    _patch_httpx(monkeypatch, handler)

    del_resp = await api_client.delete(
        f"/api/providers/models/{model_id}", params={"delete_from_disk": True}
    )
    assert del_resp.status_code == 200, del_resp.text
    assert del_resp.json()["disk_deleted"] is True
    # raw_path — байты на проводе: слэш в идентификаторе закодирован,
    # модель уходит одним сегментом пути.
    assert seen_paths == [b"/api/v1/models/org%2Frepo-GGUF"]


@pytest.mark.asyncio
async def test_delete_model_disk_unsupported_kind(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """external-провайдер: очистка с диска недоступна, метаданные удаляются."""
    await _login(api_client, app_fixture, "admin")
    provider_id = await _create_provider(api_client, "external", "http://api.test/v1")
    model_id = await _create_model(api_client, provider_id)

    resp = await api_client.delete(
        f"/api/providers/models/{model_id}", params={"delete_from_disk": True}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["disk_deleted"] is False
    assert body["disk_error"] is not None
    assert await _model_exists(app_fixture, model_id) is False
