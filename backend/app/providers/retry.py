"""Ретраи с экспоненциальной задержкой.

Только на 5xx и сетевых ошибках. 4xx не ретраится.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from app.errors import ProviderUnavailable
from app.providers.errors import ProviderTimeout, normalize_error

T = TypeVar("T")

DEFAULT_RETRIES = 3
INITIAL_DELAY = 0.5
MAX_DELAY = 4.0


def _should_retry(exc: Exception) -> bool:
    """True, если ошибка ретрится (5xx, timeout, connect error)."""
    if isinstance(exc, (ProviderUnavailable, ProviderTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return 500 <= status < 600
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))


async def with_retry[T](
    operation: Callable[[], Awaitable[T]],
    retries: int = DEFAULT_RETRIES,
    initial_delay: float = INITIAL_DELAY,
) -> T:
    """Выполняет операцию с ретраями.

    Ретраит только на 5xx и сетевых ошибках.
    4xx — немедленный отказ.
    """
    last_exc: Exception | None = None
    delay = initial_delay

    for attempt in range(retries):
        try:
            return await operation()
        except Exception as exc:
            last_exc = exc
            if not _should_retry(exc) or attempt == retries - 1:
                raise normalize_error(exc) from exc
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_DELAY)

    # unreachable, but satisfies mypy
    assert last_exc is not None
    raise normalize_error(last_exc)
