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


class DatabaseTemporarilyUnavailable(OrqionError):
    error_code = "db_temporarily_unavailable"
    reason = "База данных временно недоступна"
    status_code = 503


class NotFound(OrqionError):
    error_code = "not_found"
    reason = "Объект не найден"
    status_code = 404


class NoRouteAvailable(OrqionError):
    error_code = "no_route_available"
    reason = "Нет доступного маршрута для запроса"
    status_code = 503


class ConfigurationError(OrqionError):
    error_code = "configuration_error"
    reason = "Ошибка конфигурации"
    status_code = 500


class FileTooLarge(OrqionError):
    error_code = "file_too_large"
    reason = "Размер файла превышает допустимый лимит"
    status_code = 413


class FileTypeNotAllowed(OrqionError):
    error_code = "file_type_not_allowed"
    reason = "Тип файла не поддерживается"
    status_code = 415


class DuplicateDocument(OrqionError):
    error_code = "duplicate_document"
    reason = "Документ с таким содержимым уже загружен"
    status_code = 409


class IndexVersionGone(OrqionError):
    error_code = "index_version_gone"
    reason = "Версия индекса удалена и не может быть восстановлена"
    status_code = 409


class Forbidden(OrqionError):
    error_code = "forbidden"
    reason = "Доступ запрещён политикой роли"
    status_code = 403


class AgentRunLimitExceeded(OrqionError):
    """Т-502 (решение 4): лимит прогона агентного цикла исчерпан.

    Дополнительный предохранитель поверх обычного биллинга: число
    вызовов модели или суммарные токены за прогон превысили дефолты
    конфигурации (agent_max_steps / agent_max_tokens_per_run).
    """

    error_code = "agent_run_limit_exceeded"
    reason = "Агентный прогон остановлен: исчерпан лимит шагов или токенов"
    status_code = 400


class CorpusNotReady(OrqionError):
    error_code = "corpus_not_ready"
    reason = "Корпус не имеет активной версии индекса"
    status_code = 409


class BadRequest(OrqionError):
    error_code = "bad_request"
    reason = "Некорректный запрос"
    status_code = 400


class Conflict(OrqionError):
    error_code = "conflict"
    reason = "Операция конфликтует с текущим состоянием"
    status_code = 409


class FeatureNotSupported(OrqionError):
    """Функция недоступна в текущей конфигурации (честный отказ, §7.3)."""

    error_code = "feature_not_supported"
    reason = "Функция не поддерживается текущей конфигурацией"
    status_code = 501
