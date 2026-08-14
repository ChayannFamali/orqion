"""GET /metrics — Prometheus exposition format (T-407).

Unauthenticated endpoint (как /health). Регистрируется только если
settings.metrics_enabled=True. prometheus-client — extras, ленивый импорт.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> Response:
    """Prometheus exposition format. text/plain; version=0.0.4."""
    from app.metrics.registry import get_registry

    registry = get_registry()
    if registry is None:
        return PlainTextResponse(
            "# Metrics registry not initialized\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
            status_code=503,
        )

    from prometheus_client import generate_latest

    output = generate_latest(registry)
    return PlainTextResponse(
        output,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
