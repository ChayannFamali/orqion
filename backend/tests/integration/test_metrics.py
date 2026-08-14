"""T-407: Технические метрики — Prometheus exposition.

Тестируют:
- /metrics отключён по умолчанию (metrics_enabled=False → 404)
- /metrics включён → 200, text/plain, Prometheus format
- chat запрос → orqion_chat_requests_total increment
- chat запрос → orqion_chat_request_duration_seconds observe
- probe → orqion_provider_probe_total increment
- метрики не содержат user_id, email, cost, tokens (приёмка)
- RAG запрос → orqion_rag_queries_total increment
- metrics_enabled=False + prometheus-client не установлен → приложение стартует
"""

from __future__ import annotations

from collections.abc import Generator

import httpx
import pytest

# Тесты метрик требуют prometheus-client (extras [metrics]).
# Основной CI job (без extras) — модуль пропускается.
# metrics-gate job (pip install -e ".[metrics,dev]") — выполняется полностью.
pytest.importorskip("prometheus_client")

from app.metrics.registry import (
    get_registry,
    init_metrics,
    record_chat_request,
    record_provider_last_probe,
    record_provider_probe,
    record_rag_query,
    reset_registry,
)
from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _reset_metrics() -> Generator[None]:
    """Сбрасывает registry после каждого теста."""
    yield
    reset_registry()


def _generate_output() -> str:
    """Генерирует Prometheus exposition text из registry."""
    import prometheus_client

    registry = get_registry()
    assert registry is not None
    return str(prometheus_client.generate_latest(registry).decode())


# ---------------------------------------------------------------------------
# Endpoint tests (require prometheus_client — skipped without extras)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_enabled() -> None:
    """metrics_enabled=True → /metrics 200, text/plain, Prometheus format."""
    init_metrics()

    import app.api.metrics

    test_app = FastAPI()
    test_app.include_router(app.api.metrics.router)

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        body = resp.text
        assert "orqion_chat_requests_total" in body
        assert "orqion_chat_request_duration_seconds" in body
        assert "orqion_provider_probe_total" in body
        assert "orqion_rag_queries_total" in body


@pytest.mark.asyncio
async def test_chat_request_counter() -> None:
    """Chat запрос → orqion_chat_requests_total increment."""
    init_metrics()
    record_chat_request(status="ok", error_code="", duration_seconds=0.5)

    output = _generate_output()
    assert "orqion_chat_requests_total" in output
    assert 'status="ok"' in output


@pytest.mark.asyncio
async def test_chat_request_histogram() -> None:
    """Chat запрос → orqion_chat_request_duration_seconds observe."""
    init_metrics()
    record_chat_request(status="ok", error_code="", duration_seconds=2.5)

    output = _generate_output()
    assert "orqion_chat_request_duration_seconds" in output
    assert "_bucket" in output


@pytest.mark.asyncio
async def test_provider_probe_counter() -> None:
    """Probe → orqion_provider_probe_total increment + gauge set."""
    init_metrics()
    record_provider_probe(provider_kind="openai", status="ok", available_models=3)
    record_provider_last_probe(provider_kind="openai", timestamp_seconds=1700000000.0)

    output = _generate_output()
    assert "orqion_provider_probe_total" in output
    assert 'provider_kind="openai"' in output
    assert 'status="ok"' in output
    assert "orqion_provider_available_models" in output
    assert "orqion_provider_last_probe_timestamp_seconds" in output


@pytest.mark.asyncio
async def test_no_user_or_cost_labels() -> None:
    """Приёмка: метрики не содержат user_id, email, cost, tokens."""
    init_metrics()
    record_chat_request(status="ok", error_code="", duration_seconds=1.0)
    record_chat_request(status="error", error_code="provider_timeout", duration_seconds=5.0)
    record_provider_probe(provider_kind="openai", status="ok", available_models=2)
    record_rag_query(status="ok")
    record_rag_query(status="error")

    output = _generate_output()
    forbidden = [
        "user_id",
        "email",
        "cost",
        "tokens_in",
        "tokens_out",
        "conversation_id",
        "message_id",
    ]
    for term in forbidden:
        assert term not in output, f"Метрики утечки: '{term}' найден в output"


@pytest.mark.asyncio
async def test_rag_queries_counter() -> None:
    """RAG запрос → orqion_rag_queries_total increment."""
    init_metrics()
    record_rag_query(status="ok")
    record_rag_query(status="error")

    output = _generate_output()
    assert "orqion_rag_queries_total" in output
    assert 'status="ok"' in output
    assert 'status="error"' in output
