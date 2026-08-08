"""Тест probe: измерение возможностей, сверка upstream_name, unavailable модели."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider
from app.providers.client import ProviderClient
from app.providers.probe import probe_provider


def _make_provider_and_models() -> tuple[Provider, list[Model]]:
    provider = Provider(
        workspace_id="ws-1",
        kind="openai",
        base_url="http://stub:1234/v1",
        api_key_enc=encrypt_api_key("sk-test", "secret"),
        enabled=True,
        capabilities={},
    )
    provider.id = "prov-1"

    model_available = Model(
        workspace_id="ws-1",
        provider_id="prov-1",
        alias="local/qwen3-8b",
        upstream_name="qwen2.5-coder-7b-instruct",
        locality="local",
        enabled=True,
    )
    model_available.id = "model-1"

    model_missing = Model(
        workspace_id="ws-1",
        provider_id="prov-1",
        alias="local/missing",
        upstream_name="nonexistent-model",
        locality="local",
        enabled=True,
    )
    model_missing.id = "model-2"

    return provider, [model_available, model_missing]


def _make_client(provider: Provider, handler: Any) -> ProviderClient:
    """Создаёт ProviderClient с mock-транспортом."""
    transport = httpx.MockTransport(handler)
    client = ProviderClient(provider, "secret", timeout=15.0)
    client._client = lambda timeout=None: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=transport,
        timeout=timeout or 15.0,
        headers=client._headers(),
    )
    return client


@pytest.mark.asyncio
async def test_probe_finds_available_and_unavailable_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """upstream_name присутствует в /v1/models → available, отсутствует → unavailable."""
    provider, models = _make_provider_and_models()

    sse_lines = [
        'data: {"choices":[{"delta":{"content":"Hi"}}]}',
        "data: [DONE]",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "qwen2.5-coder-7b-instruct"}, {"id": "other-model"}]},
            )
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            if body.get("stream"):
                content = "\n".join(sse_lines) + "\n\n"
                return httpx.Response(200, content=content.encode())
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
            )
        return httpx.Response(404)

    mock_client = _make_client(provider, handler)

    def _make_client_override(prov: Provider, key: str, timeout: float = 15.0) -> ProviderClient:
        return mock_client

    monkeypatch.setattr("app.providers.probe.ProviderClient", _make_client_override)

    result = await probe_provider(provider, models, "secret")

    assert result.error is None
    assert "qwen2.5-coder-7b-instruct" in result.available_models
    assert "other-model" in result.available_models

    statuses = {s.alias: s.status for s in result.model_statuses}
    assert statuses["local/qwen3-8b"] == "available"
    assert statuses["local/missing"] == "unavailable"


@pytest.mark.asyncio
async def test_probe_provider_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Недоступный провайдер → error, все модели unavailable."""
    provider, models = _make_provider_and_models()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    mock_client = _make_client(provider, handler)

    def _make_client_override(prov: Provider, key: str, timeout: float = 15.0) -> ProviderClient:
        return mock_client

    monkeypatch.setattr("app.providers.probe.ProviderClient", _make_client_override)

    result = await probe_provider(provider, models, "secret")

    assert result.error is not None
    assert result.max_parallel == 0
    assert len(result.model_statuses) == 2
    assert all(s.status == "unavailable" for s in result.model_statuses)


@pytest.mark.asyncio
async def test_probe_measures_streaming_and_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Поддержка стриминга и параллелизма измеряется."""
    provider, models = _make_provider_and_models()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen2.5-coder-7b-instruct"}]})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            if body.get("stream"):
                return httpx.Response(
                    200,
                    content=b'data: {"choices":[{"delta":{"content":"Hi"}}]}\ndata: [DONE]\n\n',
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
            )
        return httpx.Response(404)

    mock_client = _make_client(provider, handler)

    def _make_client_override(prov: Provider, key: str, timeout: float = 15.0) -> ProviderClient:
        return mock_client

    monkeypatch.setattr("app.providers.probe.ProviderClient", _make_client_override)

    result = await probe_provider(provider, models, "secret")

    assert result.supports_streaming is True
    assert result.max_parallel == 2
