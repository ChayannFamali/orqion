"""T-444: диагностика окружения (read-only).

Приёмка: отсутствие nvidia-smi не ломает страницу (graceful «недоступно»);
гейт view_diagnostics (по умолчанию только admin через "*"); раздел
только читает — никаких действий.

Вызовы подменяются заглушкой — реальный nvidia-smi не запускается.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Role, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI

NVIDIA_SMI_CSV = (
    "551.86, NVIDIA GeForce RTX 4090, 1024, 24564, 45, 12\n"
    "551.86, NVIDIA RTX A6000, [N/A], 49140, 38, 0\n"
)


async def _login(api_client: httpx.AsyncClient, app_fixture: FastAPI, role: str) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role_obj = Role(
            workspace_id=workspace_id,
            name=role,
            is_builtin=True,
            policy=BUILTIN_ROLES[role].model_dump(),
        )
        session.add(role_obj)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email=f"diag-{role}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role_obj.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


def _patch_query(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    async def _stub() -> Any:
        return result

    monkeypatch.setattr("app.diagnostics._run_nvidia_smi_query", _stub)


@pytest.mark.asyncio
async def test_environment_requires_auth(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_environment_forbidden_without_capability(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Роль без view_diagnostics — 404 (существование раздела не раскрывается)."""
    await _login(api_client, app_fixture, "developer")
    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_environment_admin_reads_gpu_metrics(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, NVIDIA_SMI_CSV)

    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 200
    body = response.json()

    nvidia = body["nvidia"]
    assert nvidia["available"] is True
    assert nvidia["driver_version"] == "551.86"
    assert len(nvidia["gpus"]) == 2

    first, second = nvidia["gpus"]
    assert first["name"] == "NVIDIA GeForce RTX 4090"
    assert first["memory_used_mib"] == 1024
    assert first["memory_total_mib"] == 24564
    assert first["temperature_c"] == 45
    assert first["utilization_percent"] == 12
    # [N/A] — честный null, не 0
    assert second["memory_used_mib"] is None
    assert second["memory_total_mib"] == 49140

    assert body["vendor_url"] == "https://www.nvidia.com/en-us/drivers/"


@pytest.mark.asyncio
async def test_environment_graceful_when_tool_missing(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет nvidia-smi — «недоступно», 200, без падения (приёмка задачи)."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)

    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 200
    body = response.json()
    assert body["nvidia"]["available"] is False
    assert body["nvidia"]["reason"]
    assert body["nvidia"]["gpus"] == []
    assert body["vendor_url"] is None


@pytest.mark.asyncio
async def test_environment_graceful_on_garbled_output(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неразбираемый вывод — «недоступно», не 500."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, "not a csv at all\n")

    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 200
    assert response.json()["nvidia"]["available"] is False


@pytest.mark.asyncio
async def test_environment_partial_row_fields_null(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нечисловые метрики — null по полю, строка остаётся в списке."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, "551.86, RTX 4090, err, 24564, 45, 12\n")

    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 200
    gpu = response.json()["nvidia"]["gpus"][0]
    assert gpu["memory_used_mib"] is None
    assert gpu["memory_total_mib"] == 24564
