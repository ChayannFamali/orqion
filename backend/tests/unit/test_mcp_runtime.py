"""Т-503: среда исполнения клиента протокола — ленивый импорт и деградация.

Позитивные тесты требуют установленный mcp (экстра ``orqion[mcp]``) и
пропускаются без него — в дефолтном профиле клиент протокола
недоступен. Тесты деградации работают в обоих окружениях: блокируют
импорт так же, как приём «без langgraph» (Т-502), и проверяют честный
отказ (паттерн Т-444/Т-505).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from app.mcp.runtime import import_mcp, is_mcp_available


def test_is_mcp_available_true_with_mcp() -> None:
    pytest.importorskip("mcp")
    # mcp установлен в окружении теста.
    assert is_mcp_available() is True


def test_import_mcp_returns_module() -> None:
    pytest.importorskip("mcp")
    module = import_mcp()
    assert module.__name__ == "mcp"


# ---------------------------------------------------------------------------
# Деградация без mcp (экстра orqion[mcp] не установлена)
# ---------------------------------------------------------------------------


@pytest.fixture
def block_mcp(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Блокирует импорт mcp независимо от его реального наличия."""
    monkeypatch.setitem(sys.modules, "mcp", None)
    yield


def test_is_mcp_available_false_without_mcp(block_mcp: None) -> None:
    assert is_mcp_available() is False


def test_import_mcp_raises_with_install_hint(block_mcp: None) -> None:
    with pytest.raises(ImportError, match="orqion\\[mcp\\]"):
        import_mcp()
