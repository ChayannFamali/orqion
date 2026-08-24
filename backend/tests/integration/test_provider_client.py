"""Тест ProviderClient: complete, stream, list_models — через stub httpx."""

from __future__ import annotations

import json

import httpx
import pytest
from app.crypto.service import encrypt_api_key
from app.db.models import Provider
from app.errors import ProviderUnavailable
from app.providers.client import ProviderClient, normalize_base_url


def _make_provider(
    base_url: str = "http://stub:1234/v1",
    api_key: str | None = "sk-test",
    secret: str = "test-secret",
) -> Provider:
    provider = Provider(
        workspace_id="ws-1",
        kind="openai",
        base_url=base_url,
        api_key_enc=encrypt_api_key(api_key, secret) if api_key else None,
        enabled=True,
        capabilities={},
    )
    provider.id = "prov-1"
    return provider


@pytest.mark.asyncio
async def test_list_models() -> None:
    """GET /v1/models — парсинг ответа."""
    provider = _make_provider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "qwen3-8b"},
                    {"id": "qwen3-14b"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = ProviderClient(provider, "test-secret")
    client._client = lambda timeout=None: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=transport,
        timeout=timeout or 30.0,
        headers=client._headers(),
    )

    models = await client.list_models()
    assert len(models) == 2
    assert models[0]["id"] == "qwen3-8b"


@pytest.mark.asyncio
async def test_complete() -> None:
    """POST /v1/chat/completions — обычный режим."""
    provider = _make_provider()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["model"] == "qwen3-8b"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    transport = httpx.MockTransport(handler)
    client = ProviderClient(provider, "test-secret")
    client._client = lambda timeout=None: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=transport,
        timeout=timeout or 30.0,
        headers=client._headers(),
    )

    result = await client.complete(
        messages=[{"role": "user", "content": "Hi"}],
        model="qwen3-8b",
    )
    assert result["choices"][0]["message"]["content"] == "Hello!"


@pytest.mark.asyncio
async def test_stream() -> None:
    """POST /v1/chat/completions с stream=true — потоковый режим."""
    provider = _make_provider()

    sse_lines = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        content = "\n".join(sse_lines) + "\n\n"
        return httpx.Response(
            200, content=content.encode(), headers={"content-type": "text/event-stream"}
        )

    transport = httpx.MockTransport(handler)
    client = ProviderClient(provider, "test-secret")
    client._client = lambda timeout=None: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=transport,
        timeout=timeout or 120.0,
        headers=client._headers(),
    )

    chunks: list[dict[str, str]] = []
    async for event in client.stream(
        messages=[{"role": "user", "content": "Hi"}], model="qwen3-8b"
    ):
        chunks.append(event)

    assert all(e["type"] == "token" for e in chunks)
    assert "".join(e["v"] for e in chunks) == "Hello world"


@pytest.mark.asyncio
async def test_stream_with_reasoning_content() -> None:
    """T-440 (Г1): дельты reasoning_content идут отдельными событиями."""
    provider = _make_provider()

    sse_lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"Let me"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":" think."}}]}',
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"tail"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        content = "\n".join(sse_lines) + "\n\n"
        return httpx.Response(
            200, content=content.encode(), headers={"content-type": "text/event-stream"}
        )

    transport = httpx.MockTransport(handler)
    client = ProviderClient(provider, "test-secret")
    client._client = lambda timeout=None: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=transport,
        timeout=timeout or 120.0,
        headers=client._headers(),
    )

    events: list[dict[str, str]] = []
    async for event in client.stream(
        messages=[{"role": "user", "content": "Hi"}], model="qwen3-8b"
    ):
        events.append(event)

    reasoning = [e for e in events if e["type"] == "reasoning"]
    tokens = [e for e in events if e["type"] == "token"]
    assert "".join(e["v"] for e in reasoning) == "Let me think.tail"
    assert "".join(e["v"] for e in tokens) == "Hello"


@pytest.mark.asyncio
async def test_provider_500_raises_unavailable() -> None:
    """500 от провайдера → ProviderUnavailable."""
    provider = _make_provider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    client = ProviderClient(provider, "test-secret")
    client._client = lambda timeout=None: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=transport,
        timeout=timeout or 30.0,
        headers=client._headers(),
    )

    with pytest.raises(ProviderUnavailable):
        await client.list_models()


@pytest.mark.asyncio
async def test_provider_without_api_key() -> None:
    """Локальный провайдер без ключа — заголовок Authorization отсутствует."""
    provider = _make_provider(api_key=None)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    transport = httpx.MockTransport(handler)
    client = ProviderClient(provider, "test-secret")
    client._client = lambda timeout=None: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=transport,
        timeout=timeout or 30.0,
        headers=client._headers(),
    )

    models = await client.list_models()
    assert len(models) == 1


def test_normalize_base_url() -> None:
    """Каноническая форма: хвостовые слэши и /v1 убираются, глубокий путь сохраняется."""
    assert normalize_base_url("http://stub:1234") == "http://stub:1234"
    assert normalize_base_url("http://stub:1234/") == "http://stub:1234"
    assert normalize_base_url("http://stub:1234/v1") == "http://stub:1234"
    assert normalize_base_url("http://stub:1234/v1/") == "http://stub:1234"
    assert normalize_base_url("http://stub:1234/proxy/v1/") == "http://stub:1234/proxy"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://stub:1234",
        "http://stub:1234/",
        "http://stub:1234/v1",
        "http://stub:1234/v1/",
    ],
)
@pytest.mark.asyncio
async def test_base_url_variants_same_final_url(base_url: str) -> None:
    """BUG-011: 4 варианта base_url → один корректный итоговый URL (точная строка)."""
    provider = _make_provider(base_url=base_url)

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    transport = httpx.MockTransport(handler)
    client = ProviderClient(provider, "test-secret")
    client._client = lambda timeout=None: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=transport,
        timeout=timeout or 30.0,
        headers=client._headers(),
    )

    await client.list_models()
    assert seen == ["http://stub:1234/v1/models"]
