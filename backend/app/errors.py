"""Доменные исключения иерархии OrqionError.

Наружу преобразуются единым обработчиком в api/exception_handlers.py.
Каждый отказ содержит: причину, применённое ограничение, действие пользователя.
"""

from __future__ import annotations

from typing import Any


class OrqionError(Exception):
    """Базовый класс всех доменных ошибок orqion."""

    error_code: str = "orqion_error"
    reason: str = "Внутренняя ошибка"
    status_code: int = 500
    constraint: dict[str, Any] | None = None
    hint: str | None = None

    def __init__(
        self,
        message: str = "",
        *,
        constraint: dict[str, Any] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        if constraint is not None:
            self.constraint = constraint
        if hint is not None:
            self.hint = hint


class DataClassViolation(OrqionError):
    error_code = "data_class_violation"
    reason = "Корпус этого класса данных не может использовать внешние модели"
    status_code = 403


class ModelNotAllowed(OrqionError):
    error_code = "model_not_allowed"
    reason = "Модель недоступна для вашей роли"
    status_code = 403


class ContextLimitExceeded(OrqionError):
    error_code = "context_limit_exceeded"
    reason = "Размер запроса превышает лимит контекста"
    status_code = 413


class BudgetExceeded(OrqionError):
    error_code = "budget_exceeded"
    reason = "Превышен бюджет запросов"
    status_code = 429


class RateLimitExceeded(OrqionError):
    error_code = "rate_limit_exceeded"
    reason = "Превышен лимит частоты запросов"
    status_code = 429


class ProviderUnavailable(OrqionError):
    error_code = "provider_unavailable"
    reason = "Провайдер недоступен"
    status_code = 503


class NotFound(OrqionError):
    error_code = "not_found"
    reason = "Объект не найден"
    status_code = 404


class ConfigurationError(OrqionError):
    error_code = "configuration_error"
    reason = "Ошибка конфигурации"
    status_code = 500
