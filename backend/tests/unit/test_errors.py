"""Тест формата ошибок: единый JSON, без стектрейсов и путей."""

from __future__ import annotations

from app.errors import (
    ContextLimitExceeded,
    ModelNotAllowed,
    NotFound,
    OrqionError,
)


def test_orqion_error_base_fields() -> None:
    err = OrqionError()
    assert err.error_code == "orqion_error"
    assert err.reason == "Внутренняя ошибка"
    assert err.status_code == 500
    assert err.constraint is None
    assert err.hint is None


def test_context_limit_exceeded_carries_constraint_and_hint() -> None:
    err = ContextLimitExceeded(
        constraint={"limit": 16000, "actual": 18500},
        hint="Сократите запрос или выберите модель с большим контекстом",
    )
    assert err.error_code == "context_limit_exceeded"
    assert err.status_code == 413
    assert err.constraint == {"limit": 16000, "actual": 18500}
    assert err.hint == "Сократите запрос или выберите модель с большим контекстом"


def test_model_not_allowed_has_403() -> None:
    err = ModelNotAllowed(
        constraint={"model": "external/claude-sonnet"},
        hint="Доступные модели: local/*",
    )
    assert err.status_code == 403
    assert err.error_code == "model_not_allowed"


def test_not_found_has_404() -> None:
    err = NotFound()
    assert err.status_code == 404
    assert err.error_code == "not_found"


def test_error_message_does_not_leak_filesystem_paths() -> None:
    err = OrqionError("internal: /etc/passwd at C:\\secrets\\key.pem")
    assert "/etc/passwd" not in err.reason
    assert "C:\\secrets" not in err.reason
    assert err.reason == "Внутренняя ошибка"
