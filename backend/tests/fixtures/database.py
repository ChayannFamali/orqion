"""Фикстура БД: временная SQLite на каждый тест или PostgreSQL из ORQION_DATABASE_URL.

BUG-005: ранее фикстуры хардкодили SQLite, игнорируя ORQION_DATABASE_URL.
Если env var установлена (CI postgresql leg), используется PostgreSQL.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from app.config import Settings
from app.db.base import Base
from app.db.engine import create_engine
from app.db.models import Workspace  # noqa: F401 — регистрация в metadata
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)


def _get_test_database_url(tmp_path: Path) -> str:
    """Возвращает database_url для тестов.

    Если ORQION_DATABASE_URL установлена (CI postgresql leg) — использует её.
    Иначе — временный SQLite файл в tmp_path (профиль minimal, по умолчанию).
    """
    env_url = os.environ.get("ORQION_DATABASE_URL", "")
    if env_url:
        return env_url
    db_path = tmp_path / "test.db"
    return f"sqlite:///{db_path}"


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Настройки с временной SQLite или PostgreSQL из env."""
    return Settings(
        database_url=_get_test_database_url(tmp_path),
        blob_store_path=str(tmp_path / "blobs"),
        vector_store_path=str(tmp_path / "vec.db"),
        log_level="WARNING",
    )


@pytest_asyncio.fixture
async def test_engine(test_settings: Settings) -> AsyncIterator[AsyncEngine]:
    """Создаёт таблицы, отдаёт движок, уничтожает после теста.

    Использует create_engine из app.db.engine — обеспечивает PRAGMA foreign_keys=ON
    для SQLite. Для drop_all — отдельный sync engine без event listener,
    т.к. PRAGMA foreign_keys=ON препятствует DROP TABLE при наличии FK.
    Для PostgreSQL — drop_all через sync engine с psycopg2.
    """
    engine = create_engine(test_settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

    if test_settings.database_url.startswith("sqlite"):
        sync_eng = create_sync_engine(test_settings.database_url)
        with sync_eng.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            Base.metadata.drop_all(conn)
        sync_eng.dispose()
    else:
        # PostgreSQL: drop_all через sync engine
        url = test_settings.database_url
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "+psycopg2")
        sync_eng = create_sync_engine(url)
        with sync_eng.connect() as conn:
            Base.metadata.drop_all(conn)
        sync_eng.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия с автоматическим откатом после каждого теста."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
        await session.rollback()
