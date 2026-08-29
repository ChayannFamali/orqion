"""Версия проекта (Т-608).

Единственный источник версии в рантайме — метаданные установленного
пакета (``pyproject.toml`` → дистрибутив при установке). Источники в
репозитории — ``pyproject.toml`` и ``frontend/package.json``; их
равенство с метаданными проверяется юнит-тестом. Если пакет не
установлен (запуск из исходников без установки) — сообщается версия
разработки.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    try:
        return version("orqion")
    except PackageNotFoundError:
        return "0.0.0-dev"
