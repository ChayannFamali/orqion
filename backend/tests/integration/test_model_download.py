"""T-437: единый контракт скачивания моделей (часть А) + обогащение probe (часть Б).

Контракт:
- POST /api/providers/{id}/download-models — старт (202 = задание принято,
  200 = терминальный статус сразу);
- GET  /api/providers/{id}/download-status/{job_id} — поллинг.

Ollama: orqion сам читает NDJSON-стрим POST /api/pull в фоновой задаче,
прогресс в in-memory реестре. LM Studio: проксирование нативного
download-API. httpx перехватывается MockTransport (прецедент:
test_embedding_provider_backend) — CI не бьёт в реальные провайдеры.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Provider, Role, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI
from sqlalchemy import select

TERMINAL_STATUSES = ("completed", "error", "already_downloaded")


async def _login_admin(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
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
            email="t437-admin@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


async def _login_as_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
) -> None:
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
            email=f"t437-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


async def _create_provider(
    api_client: httpx.AsyncClient,
    kind: str,
    base_url: str,
) -> str:
    resp = await api_client.post(
        "/api/providers",
        json={"kind": kind, "base_url": base_url, "enabled": True},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Перехватывает все новые httpx.AsyncClient через MockTransport."""
    transport = httpx.MockTransport(handler)

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)


async def _poll_until_terminal(
    api_client: httpx.AsyncClient,
    provider_id: str,
    job_id: str,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    last: dict[str, Any] = {}
    while loop.time() < deadline:
        resp = await api_client.get(f"/api/providers/{provider_id}/download-status/{job_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] in TERMINAL_STATUSES:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"Скачивание не завершилось за {timeout_s}s: {last}")


# ---------------------------------------------------------------------------
# Ollama: orqion сам читает NDJSON-стрим /api/pull
# ---------------------------------------------------------------------------

OLLAMA_NDJSON_SUCCESS = (
    '{"status":"pulling manifest"}\n'
    '{"status":"downloading","digest":"sha256:aaa","total":100,"completed":40}\n'
    '{"status":"downloading","digest":"sha256:bbb","total":100,"completed":10}\n'
    '{"status":"downloading","digest":"sha256:aaa","total":100,"completed":100}\n'
    '{"status":"downloading","digest":"sha256:bbb","total":100,"completed":100}\n'
    '{"status":"verifying sha256 digest"}\n'
    '{"status":"writing manifest"}\n'
    '{"status":"success"}\n'
)


def _ollama_success_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/api/pull"
    return httpx.Response(
        200,
        content=OLLAMA_NDJSON_SUCCESS.encode(),
        headers={"Content-Type": "application/x-ndjson"},
    )


@pytest.mark.asyncio
async def test_ollama_download_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Старт → 202 + job_id; поллинг → completed, percent=100."""
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "ollama", "http://localhost:11434")
    _patch_httpx(monkeypatch, _ollama_success_handler)

    resp = await api_client.post(
        f"/api/providers/{provider_id}/download-models",
        json={"model": "llama3.2:1b"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"]
    assert body["status"] == "pending"

    final = await _poll_until_terminal(api_client, provider_id, body["job_id"])
    assert final["status"] == "completed"
    assert final["percent"] == 100.0
    assert final["error"] is None


def _ollama_error_handler(request: httpx.Request) -> httpx.Response:
    content = (
        b'{"status":"pulling manifest"}\n'
        b'{"error":"model \\"nope\\" not found, try pulling first"}\n'
    )
    return httpx.Response(200, content=content, headers={"Content-Type": "application/x-ndjson"})


@pytest.mark.asyncio
async def test_ollama_download_error_event(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Строка {"error": ...} в NDJSON → статус error с текстом ошибки."""
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "ollama", "http://localhost:11434")
    _patch_httpx(monkeypatch, _ollama_error_handler)

    resp = await api_client.post(
        f"/api/providers/{provider_id}/download-models",
        json={"model": "nope"},
    )
    assert resp.status_code == 202

    final = await _poll_until_terminal(api_client, provider_id, resp.json()["job_id"])
    assert final["status"] == "error"
    assert final["error"] is not None
    assert "not found" in final["error"]


@pytest.mark.asyncio
async def test_ollama_unknown_job_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "ollama", "http://localhost:11434")

    resp = await api_client.get(f"/api/providers/{provider_id}/download-status/nonexistent-job")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ollama_job_scoped_to_provider(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """job_id одного провайдера не отдаётся через другой (404)."""
    await _login_admin(api_client, app_fixture)
    provider_a = await _create_provider(api_client, "ollama", "http://localhost:11434")
    provider_b = await _create_provider(api_client, "ollama", "http://localhost:21434")
    _patch_httpx(monkeypatch, _ollama_success_handler)

    resp = await api_client.post(
        f"/api/providers/{provider_a}/download-models",
        json={"model": "llama3.2:1b"},
    )
    job_id = resp.json()["job_id"]

    cross = await api_client.get(f"/api/providers/{provider_b}/download-status/{job_id}")
    assert cross.status_code == 404


# ---------------------------------------------------------------------------
# LM Studio: проксирование нативного download-API
# ---------------------------------------------------------------------------


def _lmstudio_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/models/download" and request.method == "POST":
        return httpx.Response(
            200,
            json={
                "job_id": "job_abc123",
                "status": "downloading",
                "total_size_bytes": 1000,
                "started_at": "2026-08-23T10:00:00.000Z",
            },
        )
    if request.url.path == "/api/v1/models/download/status/job_abc123":
        return httpx.Response(
            200,
            json={
                "job_id": "job_abc123",
                "status": "completed",
                "total_size_bytes": 1000,
                "downloaded_bytes": 1000,
                "started_at": "2026-08-23T10:00:00.000Z",
                "completed_at": "2026-08-23T10:01:00.000Z",
            },
        )
    return httpx.Response(404, json={"error": "unknown route"})


@pytest.mark.asyncio
async def test_lmstudio_download_start_and_poll(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нативный job_id транслируется; статус форвардится через тот же контракт."""
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "lmstudio", "http://localhost:1234")
    _patch_httpx(monkeypatch, _lmstudio_handler)

    resp = await api_client.post(
        f"/api/providers/{provider_id}/download-models",
        json={"model": "ibm/granite-4-micro"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"] == "job_abc123"
    assert body["status"] == "downloading"

    status_resp = await api_client.get(f"/api/providers/{provider_id}/download-status/job_abc123")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["status"] == "completed"
    assert status["percent"] == 100.0


def _lmstudio_already_downloaded_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/api/v1/models/download"
    return httpx.Response(200, json={"status": "already_downloaded"})


@pytest.mark.asyncio
async def test_lmstudio_base_url_v1_suffix_normalized(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """base_url с суффиксом /v1/ (OpenAI-совместимый путь) не дублируется:
    запрос уходит в корень сервера на /api/v1/models/download."""
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"status": "already_downloaded"})

    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "lmstudio", "http://localhost:1234/v1/")
    _patch_httpx(monkeypatch, handler)

    resp = await api_client.post(
        f"/api/providers/{provider_id}/download-models",
        json={"model": "some/model"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "already_downloaded"
    assert seen_urls == ["http://localhost:1234/api/v1/models/download"]


def _lmstudio_catch_all_handler(request: httpx.Request) -> httpx.Response:
    """Эмуляция живого поведения: 200 + {"error": ...} на нераспознанный путь."""
    return httpx.Response(
        200,
        json={"error": f"Unexpected endpoint or method. (GET {request.url.path})"},
    )


@pytest.mark.asyncio
async def test_lmstudio_200_with_error_body_surfaces_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 с телом {"error": ...} без status — явная ошибка, не «неизвестный статус»."""
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "lmstudio", "http://localhost:1234")
    _patch_httpx(monkeypatch, _lmstudio_catch_all_handler)

    resp = await api_client.get(f"/api/providers/{provider_id}/download-status/job_x")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"] is not None
    assert "Unexpected endpoint" in body["error"]


def _lmstudio_nested_error_handler(request: httpx.Request) -> httpx.Response:
    """Нативный формат ошибки LM Studio: вложенный объект {type, message}."""
    return httpx.Response(
        404,
        json={
            "error": {
                "type": "job_not_found",
                "message": "Download job with id 'gone' not found",
            }
        },
    )


@pytest.mark.asyncio
async def test_lmstudio_nested_error_object_message(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "lmstudio", "http://localhost:1234")
    _patch_httpx(monkeypatch, _lmstudio_nested_error_handler)

    resp = await api_client.get(f"/api/providers/{provider_id}/download-status/gone")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"] is not None
    assert "not found" in body["error"]
    # Текст ошибки — человекочитаемый, не repr питоновского словаря
    assert "{'type'" not in body["error"]


@pytest.mark.asyncio
async def test_lmstudio_already_downloaded_terminal_without_polling(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """already_downloaded — терминальный статус сразу, без job_id и поллинга."""
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "lmstudio", "http://localhost:1234")
    _patch_httpx(monkeypatch, _lmstudio_already_downloaded_handler)

    resp = await api_client.post(
        f"/api/providers/{provider_id}/download-models",
        json={"model": "already/there"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "already_downloaded"
    assert body["job_id"] is None


def _lmstudio_failed_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"job_id": "job_f1", "status": "failed"})


@pytest.mark.asyncio
async def test_lmstudio_failed_start_reports_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "lmstudio", "http://localhost:1234")
    _patch_httpx(monkeypatch, _lmstudio_failed_handler)

    resp = await api_client.post(
        f"/api/providers/{provider_id}/download-models",
        json={"model": "bad/model"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]


# ---------------------------------------------------------------------------
# Гейты: kind, авторизация
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_gated_by_kind(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """kind вне {ollama, lmstudio} → 400 и на старте, и на поллинге."""
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "external", "http://api.test/v1")

    start = await api_client.post(
        f"/api/providers/{provider_id}/download-models",
        json={"model": "gpt-x"},
    )
    assert start.status_code == 400
    assert start.json()["error"] == "bad_request"

    status = await api_client.get(f"/api/providers/{provider_id}/download-status/whatever")
    assert status.status_code == 400


@pytest.mark.asyncio
async def test_download_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Без manage_providers → 404 (не раскрываем существование, по прецеденту)."""
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "ollama", "http://localhost:11434")

    await _login_as_role(api_client, app_fixture, "developer")
    start = await api_client.post(
        f"/api/providers/{provider_id}/download-models",
        json={"model": "llama3.2:1b"},
    )
    assert start.status_code == 404

    status = await api_client.get(f"/api/providers/{provider_id}/download-status/any-job")
    assert status.status_code == 404


@pytest.mark.asyncio
async def test_download_requires_auth(api_client: httpx.AsyncClient) -> None:
    start = await api_client.post(
        "/api/providers/some-id/download-models",
        json={"model": "x"},
    )
    assert start.status_code == 401

    status = await api_client.get("/api/providers/some-id/download-status/job")
    assert status.status_code == 401


# ---------------------------------------------------------------------------
# Часть Б: обогащение probe-ответа available_models [{name, registered}]
# ---------------------------------------------------------------------------


def _probe_upstream_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/models":
        return httpx.Response(
            200,
            json={"data": [{"id": "llama3.2:1b"}, {"id": "phi4-mini"}]},
        )
    if request.url.path == "/v1/chat/completions":
        # Минимальный ответ: стриминг-проба не найдёт "data: "-токенов
        # (supports_streaming=False), обычная проба параллельности пройдёт.
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
        )
    return httpx.Response(404, json={"error": "unknown route"})


@pytest.mark.asyncio
async def test_probe_available_models_registered_flag(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """probe-ответ: available_models = [{name, registered}]; в БД — чистые имена."""
    await _login_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client, "ollama", "http://localhost:11434")

    model_resp = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"alias": "llama-local", "upstream_name": "llama3.2:1b"},
    )
    assert model_resp.status_code == 201

    _patch_httpx(monkeypatch, _probe_upstream_handler)
    probe = await api_client.post(f"/api/providers/{provider_id}/probe")
    assert probe.status_code == 200, probe.text
    available = probe.json()["available_models"]
    assert available == [
        {"name": "llama3.2:1b", "registered": True},
        {"name": "phi4-mini", "registered": False},
    ]

    # capabilities в БД остаются измеренным списком имён (без обогащения).
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(select(Provider).where(Provider.id == provider_id))
        provider = result.scalar_one()
        assert provider.capabilities["available_models"] == ["llama3.2:1b", "phi4-mini"]
