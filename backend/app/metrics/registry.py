"""In-process Prometheus metrics registry (T-407).

prometheus-client — extras (orqion[metrics]), не core-зависимость.
Ленивый импорт: модуль импортируется безопасно даже без установленного
prometheus-client. Метрики создаются только при вызове init_metrics().

ADR-1 single-process: registry in-process, per-worker не нужен.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Символы для метрик создаются лениво — None пока init_metrics() не вызван.
# Если metrics_enabled=False — остаются None, instrumentation no-op.
_counter_chat_requests: Any = None
_histogram_chat_duration: Any = None
_counter_provider_probe: Any = None
_gauge_provider_available_models: Any = None
_gauge_provider_last_probe: Any = None
_counter_rag_queries: Any = None
_registry: Any = None

# Custom buckets для chat latency — LLM запросы уходят до десятков секунд.
# Стандартные buckets prometheus-client рассчитаны на ~10с максимум.
_CHAT_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0)


def init_metrics() -> None:
    """Инициализирует registry и метрики. Вызывается при старте если metrics_enabled.

    Возбуждает ImportError если prometheus-client не установлен.
    """
    global _counter_chat_requests, _histogram_chat_duration
    global _counter_provider_probe, _gauge_provider_available_models
    global _gauge_provider_last_probe, _counter_rag_queries, _registry

    try:
        from prometheus_client import (
            CollectorRegistry,
            Counter,
            Gauge,
            Histogram,
        )
    except ImportError as exc:
        raise ImportError(
            "prometheus-client не установлен. "
            "Установите: pip install -e '.[metrics]' (orqion[metrics] extras)"
        ) from exc

    _registry = CollectorRegistry()

    _counter_chat_requests = Counter(
        "orqion_chat_requests_total",
        "Total chat requests by status and error code",
        labelnames=["status", "error_code"],
        registry=_registry,
    )
    _histogram_chat_duration = Histogram(
        "orqion_chat_request_duration_seconds",
        "Chat request duration in seconds",
        buckets=_CHAT_BUCKETS,
        registry=_registry,
    )
    _counter_provider_probe = Counter(
        "orqion_provider_probe_total",
        "Total provider probes by kind and status",
        labelnames=["provider_kind", "status"],
        registry=_registry,
    )
    _gauge_provider_available_models = Gauge(
        "orqion_provider_available_models",
        "Number of available models per provider kind",
        labelnames=["provider_kind"],
        registry=_registry,
    )
    _gauge_provider_last_probe = Gauge(
        "orqion_provider_last_probe_timestamp_seconds",
        "Unix timestamp of last probe per provider kind",
        labelnames=["provider_kind"],
        registry=_registry,
    )
    _counter_rag_queries = Counter(
        "orqion_rag_queries_total",
        "Total RAG queries by status",
        labelnames=["status"],
        registry=_registry,
    )

    logger.info("Prometheus metrics registry initialized")


def get_registry() -> Any:
    """Возвращает registry для generate_latest(). None если не инициализирован."""
    return _registry


def record_chat_request(status: str, error_code: str, duration_seconds: float) -> None:
    """Записывает метрику chat-запроса. No-op если metrics не инициализированы."""
    if _counter_chat_requests is not None:
        _counter_chat_requests.labels(status=status, error_code=error_code).inc()
    if _histogram_chat_duration is not None:
        _histogram_chat_duration.observe(duration_seconds)


def record_provider_probe(provider_kind: str, status: str, available_models: int) -> None:
    """Записывает метрику probe. No-op если metrics не инициализированы."""
    if _counter_provider_probe is not None:
        _counter_provider_probe.labels(provider_kind=provider_kind, status=status).inc()
    if _gauge_provider_available_models is not None:
        _gauge_provider_available_models.labels(provider_kind=provider_kind).set(available_models)


def record_provider_last_probe(provider_kind: str, timestamp_seconds: float) -> None:
    """Записывает время последнего probe. No-op если metrics не инициализированы."""
    if _gauge_provider_last_probe is not None:
        _gauge_provider_last_probe.labels(provider_kind=provider_kind).set(timestamp_seconds)


def record_rag_query(status: str) -> None:
    """Записывает метрику RAG-запроса. No-op если metrics не инициализированы."""
    if _counter_rag_queries is not None:
        _counter_rag_queries.labels(status=status).inc()


def reset_registry() -> None:
    """Сбрасывает все метрики в None. Для тестов."""
    global _counter_chat_requests, _histogram_chat_duration
    global _counter_provider_probe, _gauge_provider_available_models
    global _gauge_provider_last_probe, _counter_rag_queries, _registry

    _counter_chat_requests = None
    _histogram_chat_duration = None
    _counter_provider_probe = None
    _gauge_provider_available_models = None
    _gauge_provider_last_probe = None
    _counter_rag_queries = None
    _registry = None
