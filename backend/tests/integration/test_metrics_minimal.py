"""T-407: Метрики — тесты не требующие prometheus-client.

Эти тесты проверяют что профиль minimal (без extras [metrics])
работает корректно: метрики disabled, no-op функции не падают,
fail-fast при enabled=True без пакета.
"""

from __future__ import annotations

from typing import Any

import pytest


def test_metrics_disabled_by_default() -> None:
    """metrics_enabled=False (default) → no-op, prometheus-client не нужен."""
    from app.config import Settings

    settings = Settings()
    assert settings.metrics_enabled is False


def test_metrics_noop_without_prometheus_client() -> None:
    """record_* функции — no-op если init_metrics() не вызван.

    Проверяет что профиль minimal не зависит от prometheus-client.
    """
    from app.metrics import registry as reg

    # Все метрики None — init_metrics() не вызван
    assert reg.get_registry() is None

    # record_* — no-op, не падают
    reg.record_chat_request(status="ok", error_code="", duration_seconds=1.0)
    reg.record_provider_probe(provider_kind="test", status="ok", available_models=0)
    reg.record_provider_last_probe(provider_kind="test", timestamp_seconds=0.0)
    reg.record_rag_query(status="ok")

    # Ничего не записалось — registry всё ещё None
    assert reg.get_registry() is None


def test_init_metrics_raises_without_prometheus_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """metrics_enabled=True + prometheus-client не установлен → ImportError с подсказкой.

    Fail-fast, не тихая деградация (по аналогии с eval-gate).
    """
    import builtins

    import app.metrics.registry as reg

    real_import: Any = builtins.__import__

    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "prometheus_client":
            raise ImportError("No module named 'prometheus_client'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    with pytest.raises(ImportError) as exc_info:
        reg.init_metrics()
    assert "prometheus-client" in str(exc_info.value)
    assert "orqion[metrics]" in str(exc_info.value)
    reg.reset_registry()
