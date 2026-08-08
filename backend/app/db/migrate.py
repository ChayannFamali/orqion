"""Миграции Alembic: автоприменение при старте и команда orqion migrate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db.engine import create_engine

ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "db" / "migrations"


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
    """Применяет миграции, используя async-движок."""
    engine: AsyncEngine = create_engine(settings)

    def _do_migrations(_engine: Any) -> None:
        run_migrations_sync(settings.database_url)

    async with engine.begin() as conn:
        await conn.run_sync(_do_migrations)
    await engine.dispose()
