"""Фикстура БД: временная SQLite на каждый тест."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from app.config import Settings
from app.db.base import Base
from app.db.models import Workspace  # noqa: F401 — регистрация в metadata
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Настройки с временной SQLite в tmp_path."""
    db_path = tmp_path / "test.db"
    return Settings(
        database_url=f"sqlite:///{db_path}",
        blob_store_path=str(tmp_path / "blobs"),
        log_level="WARNING",
    )


@pytest_asyncio.fixture
async def test_engine(test_settings: Settings) -> AsyncIterator[AsyncEngine]:
    """Создаёт таблицы, отдаёт движок, уничтожает после теста."""
    url = test_settings.database_url.replace("sqlite://", "sqlite+aiosqlite://")
    engine = create_async_engine(url, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия с автоматическим откатом после каждого теста."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
        await session.rollback()
