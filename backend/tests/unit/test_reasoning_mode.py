"""Т-445 (каркас): матрица эффективного режима рассуждения (А3).

``off``/``on`` фиксируются политикой роли; при ``optional`` учитывается
выбор на уровне сообщения (Г1), по умолчанию ``auto``.
"""

from __future__ import annotations

from app.api.routes.chat import _effective_reasoning_mode


def test_policy_off_fixed() -> None:
    assert _effective_reasoning_mode("off", None) == "off"
    # Выбор на сообщении игнорируется при фиксированной политике
    assert _effective_reasoning_mode("off", "on") == "off"


def test_policy_on_fixed() -> None:
    assert _effective_reasoning_mode("on", None) == "on"
    assert _effective_reasoning_mode("on", "off") == "on"


def test_optional_defaults_to_auto() -> None:
    assert _effective_reasoning_mode("optional", None) == "auto"
    # Не-значение вне (on, off) трактуется как авто
    assert _effective_reasoning_mode("optional", "bogus") == "auto"


def test_optional_respects_per_message_choice() -> None:
    assert _effective_reasoning_mode("optional", "on") == "on"
    assert _effective_reasoning_mode("optional", "off") == "off"
