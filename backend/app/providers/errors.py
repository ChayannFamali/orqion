"""Нормализация ошибок провайдеров к доменным классам."""

from __future__ import annotations

import httpx

from app.errors import OrqionError, ProviderUnavailable, RateLimitExceeded


class ProviderAuthError(OrqionError):
    error_code = "provider_auth_error"
    reason = "Ошибка аутентификации провайдера"
    status_code = 503
    hint = "Проверьте API-ключ провайдера"


class ProviderTimeout(OrqionError):
    error_code = "provider_timeout"
    reason = "Превышен таймаут ожидания ответа провайдера"
    status_code = 504
    hint = "Повторите запрос или выберите другую модель"


class ProviderBadRequest(OrqionError):
    error_code = "provider_bad_request"
    reason = "Провайдер отклонил запрос"
    status_code = 400


def normalize_error(exc: Exception) -> OrqionError:
    """Преобразует httpx-исключение или HTTP-статус в доменную ошибку orqion."""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeout(
            hint=f"Таймаут: {type(exc).__name__}",
        )

    if isinstance(exc, httpx.ConnectError):
        return ProviderUnavailable(
            hint="Провайдер недоступен: не удалось установить соединение",
        )

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401 or status == 403:
            return ProviderAuthError()
        if status == 429:
            return RateLimitExceeded(
                constraint={"type": "provider_rate_limit"},
                hint="Провайдер ограничил частоту запросов",
            )
        if 500 <= status < 600:
            return ProviderUnavailable(
                hint=f"Провайдер вернул {status}",
            )
        return ProviderBadRequest(
            hint=f"Провайдер вернул {status}",
        )

    if isinstance(exc, OrqionError):
        return exc

    return ProviderUnavailable(
        hint=f"Неизвестная ошибка: {type(exc).__name__}",
    )
