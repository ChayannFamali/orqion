"""Реестр провайдеров, единый OpenAI-совместимый адаптер, probe возможностей."""

from app.providers.client import ProviderClient
from app.providers.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderTimeout,
    normalize_error,
)
from app.providers.probe import ProbeResult, probe_provider
from app.providers.retry import with_retry

__all__ = [
    "ProbeResult",
    "ProviderAuthError",
    "ProviderBadRequest",
    "ProviderClient",
    "ProviderTimeout",
    "normalize_error",
    "probe_provider",
    "with_retry",
]
