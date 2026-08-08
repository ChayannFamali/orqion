"""Зависимость FastAPI для получения сессии БД."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Возвращает сессию с автоматическим commit/rollback."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.db_session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except:
            await session.rollback()
            raise
