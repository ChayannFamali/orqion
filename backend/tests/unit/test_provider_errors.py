"""Тест нормализации ошибок провайдеров."""

from __future__ import annotations

import httpx
from app.errors import ProviderUnavailable, RateLimitExceeded
from app.providers.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderTimeout,
    normalize_error,
)


def test_timeout_exception_becomes_provider_timeout() -> None:
    exc = httpx.ReadTimeout("read timed out")
    result = normalize_error(exc)
    assert isinstance(result, ProviderTimeout)
    assert result.status_code == 504


def test_connect_error_becomes_provider_unavailable() -> None:
    exc = httpx.ConnectError("connection refused")
    result = normalize_error(exc)
    assert isinstance(result, ProviderUnavailable)
    assert result.status_code == 503


def test_401_becomes_auth_error() -> None:
    request = httpx.Request("POST", "http://localhost/v1/chat")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("401", request=request, response=response)
    result = normalize_error(exc)
    assert isinstance(result, ProviderAuthError)
    assert result.status_code == 503


def test_429_becomes_rate_limit() -> None:
    request = httpx.Request("POST", "http://localhost/v1/chat")
    response = httpx.Response(429, request=request)
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    result = normalize_error(exc)
    assert isinstance(result, RateLimitExceeded)


def test_500_becomes_provider_unavailable() -> None:
    request = httpx.Request("POST", "http://localhost/v1/chat")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("500", request=request, response=response)
    result = normalize_error(exc)
    assert isinstance(result, ProviderUnavailable)


def test_400_becomes_bad_request() -> None:
    request = httpx.Request("POST", "http://localhost/v1/chat")
    response = httpx.Response(400, request=request)
    exc = httpx.HTTPStatusError("400", request=request, response=response)
    result = normalize_error(exc)
    assert isinstance(result, ProviderBadRequest)
    assert result.status_code == 400


def test_unknown_exception_becomes_provider_unavailable() -> None:
    exc = RuntimeError("unexpected")
    result = normalize_error(exc)
    assert isinstance(result, ProviderUnavailable)
