"""Единый OpenAI-совместимый адаптер провайдеров.

Обычный и потоковый режимы, таймауты, ретраи, нормализация ошибок.
Отдельного класса на каждый продукт не заводится (S-10).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.crypto.service import decrypt_api_key
from app.db.models import Provider
from app.providers.errors import normalize_error
from app.providers.retry import with_retry

DEFAULT_TIMEOUT = 30.0
STREAM_TIMEOUT = 120.0


class ProviderClient:
    """OpenAI-совместимый клиент для одного провайдера."""

    def __init__(
        self,
        provider: Provider,
        secret_key: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = provider.base_url.rstrip("/")
        self._secret_key = secret_key
        self._timeout = timeout

        api_key: str | None = None
        if provider.api_key_enc:
            api_key = decrypt_api_key(provider.api_key_enc, secret_key)
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout or self._timeout,
            headers=self._headers(),
        )

    async def list_models(self) -> list[dict[str, Any]]:
        """GET /v1/models — список моделей провайдера."""

        async def _call() -> list[dict[str, Any]]:
            async with self._client() as client:
                response = await client.get(f"{self._base_url}/models")
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                models: list[dict[str, Any]] = data.get("data", [])
                return models

        return await with_retry(_call)

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        """POST /v1/chat/completions — обычный (не потоковый) режим."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        async def _call() -> dict[str, Any]:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result

        return await with_retry(_call)

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """POST /v1/chat/completions с stream=true — потоковый режим.

        Возвращает AsyncIterator[str] — токены по мере поступления.
        Таймаут увеличен для потоковых запросов.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            async with (
                self._client(STREAM_TIMEOUT) as client,
                client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=payload,
                ) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            raise normalize_error(exc) from exc
