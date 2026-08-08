"""Тест GET /health."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_health_returns_200(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
