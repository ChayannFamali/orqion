"""Ограничение попыток входа (T-108a).

Sliding window в памяти: не более max_attempts за period_seconds
по ключу (email, ip). При превышении — 429 с временем сброса.
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class LoginRateLimiter:
    """Sliding window rate limiter для попыток входа.

    Ключ — (email, ip). Хранит timestamps попыток в deque.
    При превышении max_attempts за period_seconds → блокировка
    до момента, когда самая старая попытка выйдет за окно.
    """

    def __init__(self, max_attempts: int = 5, period_seconds: int = 300) -> None:
        self._max_attempts = max_attempts
        self._period = period_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def _key(self, email: str, ip: str) -> str:
        return f"{email}:{ip}"

    def _now(self) -> float:
        return time.monotonic()

    def check(self, email: str, ip: str) -> float | None:
        """Проверяет, можно ли сделать попытку входа.

        Возвращает None, если попытка разрешена.
        Иначе — секунды до сброса (когда самая старая попытка выйдет за окно).
        """
        key = self._key(email, ip)
        now = self._now()

        with self._lock:
            timestamps = self._attempts[key]
            cutoff = now - self._period
            while timestamps and timestamps[0] < cutoff:
                timestamps.pop(0)

            if len(timestamps) >= self._max_attempts:
                reset_in = timestamps[0] + self._period - now
                return max(reset_in, 0.1)

            timestamps.append(now)
            return None

    def reset(self, email: str, ip: str) -> None:
        """Сбрасывает счётчик после успешного входа."""
        key = self._key(email, ip)
        with self._lock:
            self._attempts.pop(key, None)
