"""Реестр провайдеров, единый OpenAI-совместимый адаптер, probe возможностей."""

from app.providers.client import ProviderClient
from app.providers.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderTimeout,
    normalize_error,
)
from app.providers.retry import with_retry

__all__ = [
    "ProviderAuthError",
    "ProviderBadRequest",
    "ProviderClient",
    "ProviderTimeout",
    "normalize_error",
    "with_retry",
]
