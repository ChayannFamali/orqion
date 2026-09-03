"""Т-502: среда исполнения агентного модуля — ленивый импорт и деградация.

Позитивные тесты требуют установленный langgraph (экстра
``orqion[agent]``) и пропускаются без него — в дефолтном профиле
агентный модуль недоступен. Тесты деградации работают в обоих
окружениях: блокируют импорт так же, как приём «без authlib» (Т-404),
и проверяют честный отказ (паттерн Т-505).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from app.agent.runtime import import_langgraph, is_agent_available


def test_is_agent_available_true_with_langgraph() -> None:
    pytest.importorskip("langgraph")
    # langgraph установлен в окружении теста.
    assert is_agent_available() is True


def test_import_langgraph_returns_module() -> None:
    pytest.importorskip("langgraph")
    module = import_langgraph()
    assert module.__name__ == "langgraph"


# ---------------------------------------------------------------------------
# Деградация без langgraph (экстра orqion[agent] не установлена)
# ---------------------------------------------------------------------------


@pytest.fixture
def block_langgraph(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Блокирует импорт langgraph независимо от его реального наличия."""
    monkeypatch.setitem(sys.modules, "langgraph", None)
    yield


def test_is_agent_available_false_without_langgraph(block_langgraph: None) -> None:
    assert is_agent_available() is False


def test_import_langgraph_raises_with_install_hint(block_langgraph: None) -> None:
    with pytest.raises(ImportError, match="orqion\\[agent\\]"):
        import_langgraph()
