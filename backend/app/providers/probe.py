"""Probe: измерение возможностей провайдера.

Значения измеряются, не берутся из конфигурации (ADR-6).
Сверка upstream_name с GET /v1/models — обязательно (находка из T-111).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from pydantic import BaseModel

from app.db.models import Model, Provider
from app.providers.client import ProviderClient
from app.providers.errors import normalize_error

PROBE_TIMEOUT = 15.0
PROBE_MAX_TOKENS = 5
PROBE_MESSAGES = [{"role": "user", "content": "Hi"}]


class ModelStatus(BaseModel):
    model_id: str
    alias: str
    upstream_name: str
    status: str  # "available" | "unavailable"


class ProbeResult(BaseModel):
    available_models: list[str]
    supports_streaming: bool
    max_parallel: int
    model_statuses: list[ModelStatus]
    probed_at: datetime
    error: str | None = None


async def probe_provider(
    provider: Provider,
    models: list[Model],
    secret_key: str,
) -> ProbeResult:
    """Измеряет возможности провайдера.

    Не возбуждает — возвращает ProbeResult с error при неудаче.
    """
    client = ProviderClient(provider, secret_key, timeout=PROBE_TIMEOUT)

    try:
        remote_models = await client.list_models()
    except Exception as exc:  # noqa: BLE001 — граница провайдера, ловим всё
        err = normalize_error(exc)
        return ProbeResult(
            available_models=[],
            supports_streaming=False,
            max_parallel=0,
            model_statuses=[
                ModelStatus(
                    model_id=m.id,
                    alias=m.alias,
                    upstream_name=m.upstream_name,
                    status="unavailable",
                )
                for m in models
            ],
            probed_at=datetime.now(UTC),
            error=err.reason,
        )

    available_ids = {m["id"] for m in remote_models if "id" in m}

    model_statuses = [
        ModelStatus(
            model_id=m.id,
            alias=m.alias,
            upstream_name=m.upstream_name,
            status="available" if m.upstream_name in available_ids else "unavailable",
        )
        for m in models
    ]

    first_available = next(
        (m for m in models if m.upstream_name in available_ids),
        None,
    )

    supports_streaming = False
    if first_available is not None:
        supports_streaming = await _probe_streaming(client, first_available.upstream_name)

    max_parallel = await _probe_parallel(client, first_available)

    return ProbeResult(
        available_models=sorted(available_ids),
        supports_streaming=supports_streaming,
        max_parallel=max_parallel,
        model_statuses=model_statuses,
        probed_at=datetime.now(UTC),
    )


async def _probe_streaming(client: ProviderClient, model: str) -> bool:
    """Проверяет поддержку стриминга одним пробным запросом."""
    try:
        async for _ in client.stream(
            messages=PROBE_MESSAGES,
            model=model,
            max_tokens=PROBE_MAX_TOKENS,
        ):
            break  # первый токен получен — стриминг работает
        return True
    except Exception:  # noqa: BLE001 — probe не должен падать
        return False


async def _probe_parallel(
    client: ProviderClient,
    model: Model | None,
) -> int:
    """Измеряет max_parallel: 2 параллельных запроса.

    Возвращает 2 если оба завершились успешно, 1 если один упал, 0 если модель нет.
    """
    if model is None:
        return 0

    async def _one() -> bool:
        try:
            await client.complete(
                messages=PROBE_MESSAGES,
                model=model.upstream_name,
                max_tokens=PROBE_MAX_TOKENS,
            )
            return True
        except Exception:  # noqa: BLE001 — probe не должен падать
            return False

    results = await asyncio.gather(_one(), _one(), return_exceptions=True)
    success = sum(1 for r in results if r is True)
    return 2 if success == 2 else (1 if success == 1 else 0)
