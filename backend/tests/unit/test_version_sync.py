"""Т-608: версия проекта синхронна во всех трёх местах.

Источники версии в репозитории — ``pyproject.toml`` и
``frontend/package.json``; в рантайме версия берётся из метаданных
установленного пакета (``app.version.get_version``). Тест ловит дрейф:
если версия поднята в одном месте и не поднята в другом, он красный.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest
from app.cli import main
from app.version import get_version

REPO_ROOT = Path(__file__).resolve().parents[3]


def _pyproject_version() -> str:
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return str(data["project"]["version"])


def _frontend_version() -> str:
    raw = (REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    return str(json.loads(raw)["version"])


def test_pyproject_matches_frontend_package_json() -> None:
    assert _pyproject_version() == _frontend_version()


def test_installed_metadata_matches_pyproject() -> None:
    assert get_version() == _pyproject_version()


def test_cli_version_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """orqion --version печатает версию и выходит с кодом 0."""
    monkeypatch.setattr(sys, "argv", ["orqion", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert f"orqion {get_version()}" in out
