"""Асинхронный движок SQLAlchemy и фабрика сессий."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Создаёт async-движок из URL настроек.

    Преобразует bare sqlite:// и postgresql:// в async-варианты,
    добавляя драйвер +aiosqlite / +asyncpg.
    """
    url = settings.database_url
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        url = url.replace("sqlite://", "sqlite+aiosqlite://")
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://")

    engine_kwargs: dict[str, Any] = {"echo": settings.log_level == "DEBUG"}
    if url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_async_engine(url, **engine_kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_conn: Any, _: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Контекст транзакции: commit при успехе, rollback при исключении."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except:
            await session.rollback()
            raise
