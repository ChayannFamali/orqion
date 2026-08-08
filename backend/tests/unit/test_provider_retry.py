"""Тест логики ретраев."""

from __future__ import annotations

import httpx
import pytest
from app.errors import ProviderUnavailable
from app.providers.errors import ProviderBadRequest
from app.providers.retry import _should_retry, with_retry


def test_should_retry_500() -> None:
    request = httpx.Request("POST", "http://localhost")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("500", request=request, response=response)
    assert _should_retry(exc) is True


def test_should_not_retry_400() -> None:
    request = httpx.Request("POST", "http://localhost")
    response = httpx.Response(400, request=request)
    exc = httpx.HTTPStatusError("400", request=request, response=response)
    assert _should_retry(exc) is False


def test_should_retry_timeout() -> None:
    assert _should_retry(httpx.ReadTimeout("timeout")) is True


def test_should_retry_connect_error() -> None:
    assert _should_retry(httpx.ConnectError("refused")) is True


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt() -> None:
    """Первый вызов — 503, второй — успех."""
    call_count = 0

    async def operation() -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            request = httpx.Request("POST", "http://localhost")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("503", request=request, response=response)
        return "ok"

    result = await with_retry(operation, retries=3, initial_delay=0.01)
    assert result == "ok"
    assert call_count == 2


@pytest.mark.asyncio
async def test_no_retry_on_4xx() -> None:
    """4xx — немедленный отказ без ретраев."""
    call_count = 0

    async def operation() -> str:
        nonlocal call_count
        call_count += 1
        request = httpx.Request("POST", "http://localhost")
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("400", request=request, response=response)

    with pytest.raises(ProviderBadRequest):
        await with_retry(operation, retries=3, initial_delay=0.01)
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_exhausted_raises_provider_unavailable() -> None:
    """Все попытки исчерпаны — ProviderUnavailable."""
    call_count = 0

    async def operation() -> str:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderUnavailable):
        await with_retry(operation, retries=3, initial_delay=0.01)
    assert call_count == 3
