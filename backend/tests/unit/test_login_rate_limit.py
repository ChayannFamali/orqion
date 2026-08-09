"""Тесты ограничения попыток входа (T-108a).

Проверки:
- до лимита: попытки проходят (401 за неверный пароль, не 429)
- на превышении: 429 с reset_in_seconds
- успешный вход сбрасывает счётчик
- блокировка по email+ip, другой email не блокируется
- разные IP для одного email — отдельные счётчики
"""

from __future__ import annotations

import time

from app.auth.rate_limit import LoginRateLimiter


def test_under_limit_allows() -> None:
    limiter = LoginRateLimiter(max_attempts=3, period_seconds=60)
    assert limiter.check("user@orqion.local", "127.0.0.1") is None
    assert limiter.check("user@orqion.local", "127.0.0.1") is None
    assert limiter.check("user@orqion.local", "127.0.0.1") is None


def test_over_limit_returns_reset_time() -> None:
    limiter = LoginRateLimiter(max_attempts=3, period_seconds=60)
    limiter.check("user@orqion.local", "127.0.0.1")
    limiter.check("user@orqion.local", "127.0.0.1")
    limiter.check("user@orqion.local", "127.0.0.1")

    reset_in = limiter.check("user@orqion.local", "127.0.0.1")
    assert reset_in is not None
    assert reset_in > 0
    assert reset_in <= 60


def test_successful_login_resets_counter() -> None:
    limiter = LoginRateLimiter(max_attempts=3, period_seconds=60)
    limiter.check("user@orqion.local", "127.0.0.1")
    limiter.check("user@orqion.local", "127.0.0.1")
    limiter.reset("user@orqion.local", "127.0.0.1")

    # После сброса — снова 3 попытки
    assert limiter.check("user@orqion.local", "127.0.0.1") is None
    assert limiter.check("user@orqion.local", "127.0.0.1") is None
    assert limiter.check("user@orqion.local", "127.0.0.1") is None


def test_different_email_not_blocked() -> None:
    limiter = LoginRateLimiter(max_attempts=2, period_seconds=60)
    limiter.check("user1@orqion.local", "127.0.0.1")
    limiter.check("user1@orqion.local", "127.0.0.1")
    assert limiter.check("user1@orqion.local", "127.0.0.1") is not None

    # Другой email — не заблокирован
    assert limiter.check("user2@orqion.local", "127.0.0.1") is None


def test_different_ip_not_blocked() -> None:
    limiter = LoginRateLimiter(max_attempts=2, period_seconds=60)
    limiter.check("user@orqion.local", "127.0.0.1")
    limiter.check("user@orqion.local", "127.0.0.1")
    assert limiter.check("user@orqion.local", "127.0.0.1") is not None

    # Другой IP — не заблокирован
    assert limiter.check("user@orqion.local", "192.168.1.1") is None


def test_window_expires() -> None:
    """После истечения окна счётчик сбрасывается автоматически."""
    limiter = LoginRateLimiter(max_attempts=2, period_seconds=1)
    limiter.check("user@orqion.local", "127.0.0.1")
    limiter.check("user@orqion.local", "127.0.0.1")
    assert limiter.check("user@orqion.local", "127.0.0.1") is not None

    time.sleep(1.1)
    assert limiter.check("user@orqion.local", "127.0.0.1") is None
