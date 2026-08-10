"""Миграции Alembic: автоприменение при старте и команда orqion migrate."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import Settings

ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent.parent / "alembic.ini"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _make_alembic_config(database_url: str) -> Config:
    """Создаёт конфиг Alembic с подменой URL на async-вариант."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))

    url = database_url
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        url = url.replace("sqlite://", "sqlite+aiosqlite://")
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://")

    config.set_main_option("sqlalchemy.url", url)
    return config


def run_migrations_sync(database_url: str) -> None:
    """Синхронно применяет миграции до head."""
    config = _make_alembic_config(database_url)
    command.upgrade(config, "head")


async def run_migrations(settings: Settings) -> None:
    """Применяет миграции в отдельном потоке.

    env.py вызывает asyncio.run() для async-движка Alembic, поэтому
    синхронный запуск из async-контекста требует отдельный поток —
    иначе asyncio.run падает с «cannot be called from a running event loop».
    Аналогично main.py lifespan, который использует asyncio.to_thread.
    """
    await asyncio.to_thread(run_migrations_sync, settings.database_url)
