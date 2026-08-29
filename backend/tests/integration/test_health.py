"""Тест GET /health, GET /ready."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app.version import get_version
from fastapi import FastAPI


@pytest.mark.asyncio
async def test_health_returns_200(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_reports_version(api_client: httpx.AsyncClient) -> None:
    """Т-608: /health сообщает версию — сверка с тегом выпуска без парсинга файлов."""
    response = await api_client.get("/health")
    assert response.json()["version"] == get_version()


@pytest.mark.asyncio
async def test_ready_returns_200(api_client: httpx.AsyncClient) -> None:
    """Все три зависимости доступны → 200 ready."""
    response = await api_client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_ready_db_failure_503(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """DB недоступна → 503 с указанием database в failures."""
    original_engine = app_fixture.state.db_engine
    broken_engine = MagicMock()
    broken_engine.connect = MagicMock(side_effect=RuntimeError("connection refused"))
    app_fixture.state.db_engine = broken_engine

    response = await api_client.get("/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    components = [f["component"] for f in data["failures"]]
    assert "database" in components

    app_fixture.state.db_engine = original_engine


@pytest.mark.asyncio
async def test_ready_vector_store_failure_503(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Vector store недоступен → 503 с указанием vector_store."""
    original_store = app_fixture.state.vector_store
    broken_store = MagicMock()
    broken_store._get_conn = AsyncMock(side_effect=RuntimeError("vec.db corrupted"))
    app_fixture.state.vector_store = broken_store

    response = await api_client.get("/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    components = [f["component"] for f in data["failures"]]
    assert "vector_store" in components

    app_fixture.state.vector_store = original_store


@pytest.mark.asyncio
async def test_ready_blob_store_failure_503(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Blob store недоступен → 503 с указанием blob_store."""
    original_store = app_fixture.state.blob_store
    broken_store = MagicMock()
    broken_store._root = "/nonexistent/path/that/does/not/exist"
    app_fixture.state.blob_store = broken_store

    response = await api_client.get("/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    components = [f["component"] for f in data["failures"]]
    assert "blob_store" in components

    app_fixture.state.blob_store = original_store


@pytest.mark.asyncio
async def test_ready_multiple_failures_503(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Несколько зависимостей недоступны → 503 с перечнем всех отказов."""
    original_engine = app_fixture.state.db_engine
    original_store = app_fixture.state.vector_store

    broken_engine = MagicMock()
    broken_engine.connect = MagicMock(side_effect=RuntimeError("db down"))
    app_fixture.state.db_engine = broken_engine

    broken_store = MagicMock()
    broken_store._get_conn = AsyncMock(side_effect=RuntimeError("vec down"))
    app_fixture.state.vector_store = broken_store

    response = await api_client.get("/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    components = [f["component"] for f in data["failures"]]
    assert "database" in components
    assert "vector_store" in components
    assert len(data["failures"]) >= 2

    app_fixture.state.db_engine = original_engine
    app_fixture.state.vector_store = original_store
