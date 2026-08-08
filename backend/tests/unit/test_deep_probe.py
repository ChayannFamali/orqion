"""Тест измерения фактического контекста (deep probe)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider
from app.providers.client import ProviderClient
from app.providers.deep_probe import _make_prompt, measure_observed_context


def _make_provider() -> tuple[Provider, Model]:
    provider = Provider(
        workspace_id="ws-1",
        kind="openai",
        base_url="http://stub:1234/v1",
        api_key_enc=encrypt_api_key("sk-test", "secret"),
        enabled=True,
        capabilities={},
    )
    provider.id = "prov-1"

    model = Model(
        workspace_id="ws-1",
        provider_id="prov-1",
        alias="local/qwen3-8b",
        upstream_name="qwen3-8b",
        locality="local",
        max_input_tokens=4096,
        enabled=True,
    )
    model.id = "model-1"

    return provider, model


def _make_client(provider: Provider, handler: Any) -> ProviderClient:
    transport = httpx.MockTransport(handler)
    client = ProviderClient(provider, "secret", timeout=60.0)
    client._client = lambda timeout=None: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=transport,
        timeout=timeout or 60.0,
        headers=client._headers(),
    )
    return client


def test_make_prompt_approximate_size() -> None:
    """Промпт приблизительно token_estimate токенов."""
    prompt = _make_prompt(100)
    # ~4 символа на токен, ~44 символа в filler
    assert len(prompt) >= 100 * 4 * 0.5  # не меньше половины оценки


@pytest.mark.asyncio
async def test_measure_context_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Модель принимает 4096 токенов — observed_context >= 4096."""
    provider, model = _make_provider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    mock_client = _make_client(provider, handler)

    def _make_client_override(prov: Provider, key: str, timeout: float = 60.0) -> ProviderClient:
        return mock_client

    monkeypatch.setattr("app.providers.deep_probe.ProviderClient", _make_client_override)

    result = await measure_observed_context(provider, model, "secret")
    assert result is not None
    # Бинарный поиск с 4 итерациями сходится к ~3856, не точно к 4096
    assert result > 3000


@pytest.mark.asyncio
async def test_measure_context_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Модель отказывает на больших промптах — observed_context ниже max_input_tokens."""
    provider, model = _make_provider()
    model.max_input_tokens = 4096

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        if len(prompt) > 8000:
            return httpx.Response(400, json={"error": "context_length_exceeded"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    mock_client = _make_client(provider, handler)

    def _make_client_override(prov: Provider, key: str, timeout: float = 60.0) -> ProviderClient:
        return mock_client

    monkeypatch.setattr("app.providers.deep_probe.ProviderClient", _make_client_override)

    result = await measure_observed_context(provider, model, "secret")
    assert result is not None
    assert result < 4096


@pytest.mark.asyncio
async def test_measure_context_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не более 4 попыток — проверка лимита."""
    provider, model = _make_provider()

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(422, json={"error": "bad"})

    mock_client = _make_client(provider, handler)

    def _make_client_override(prov: Provider, key: str, timeout: float = 60.0) -> ProviderClient:
        return mock_client

    monkeypatch.setattr("app.providers.deep_probe.ProviderClient", _make_client_override)

    result = await measure_observed_context(provider, model, "secret")
    assert result is None  # все попытки провалились
    # 4 итерации × 1 вызов (422 = 4xx, не ретраится) = 4
    assert call_count == 4
