"""In-process Prometheus metrics (T-407). Extras: orqion[metrics]."""

from __future__ import annotations

from app.metrics.registry import (
    get_registry,
    init_metrics,
    record_chat_request,
    record_provider_last_probe,
    record_provider_probe,
    record_rag_query,
)

__all__ = [
    "get_registry",
    "init_metrics",
    "record_chat_request",
    "record_provider_last_probe",
    "record_provider_probe",
    "record_rag_query",
]
