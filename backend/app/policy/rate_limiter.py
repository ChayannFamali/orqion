"""In-memory rate limiter: sliding window для RPM и TPM.

Однопроцессная реализация для профиля minimal. Для multi-process (standard/full)
заменяется на Redis-бэкенд — интерфейс сохраняется.
"""

from __future__ import annotations

import time
from collections import defaultdict

WINDOW_SECONDS = 60.0


class RateLimiter:
    """Sliding window rate limiter для RPM (запросы) и TPM (токены)."""

    def __init__(self) -> None:
        self._rpm_windows: dict[str, list[float]] = defaultdict(list)
        self._tpm_windows: dict[str, list[tuple[float, int]]] = defaultdict(list)

    def check_rpm(self, user_id: str, rpm: int) -> float | None:
        """Проверяет RPM. Возвращает секунды до сброса при превышении, None если OK.

        Записывает запрос в окно только если проверка пройдена.
        """
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS
        window = [t for t in self._rpm_windows[user_id] if t > cutoff]
        self._rpm_windows[user_id] = window

        if len(window) >= rpm:
            oldest = window[0]
            return oldest + WINDOW_SECONDS - now

        window.append(now)
        self._rpm_windows[user_id] = window
        return None

    def check_tpm(
        self,
        user_id: str,
        tokens: int,
        tpm: int,
    ) -> float | None:
        """Проверяет TPM. Возвращает секунды до сброса при превышении, None если OK.

        Записывает токены в окно только если проверка пройдена.
        """
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS
        window = [(t, tok) for t, tok in self._tpm_windows[user_id] if t > cutoff]
        self._tpm_windows[user_id] = window

        current_sum = sum(tok for _, tok in window)
        if current_sum + tokens > tpm:
            oldest = window[0][0] if window else now
            return oldest + WINDOW_SECONDS - now

        window.append((now, tokens))
        self._tpm_windows[user_id] = window
        return None

    def reset(self, user_id: str) -> None:
        """Сбрасывает счётчики пользователя (для тестов)."""
        self._rpm_windows.pop(user_id, None)
        self._tpm_windows.pop(user_id, None)
