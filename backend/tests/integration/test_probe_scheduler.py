"""Тест probe_scheduler: запуск, отмена, обработка ошибок."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from app.db.workspace import ensure_default_workspace
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.asyncio
async def test_probe_scheduler_cancellable(db_session: AsyncSession) -> None:
    """Планировщик отменяется через CancelledError, не оставляет висящих задач."""
    await ensure_default_workspace(db_session)
    await db_session.flush()

    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    from app.providers.probe_scheduler import probe_scheduler

    task = asyncio.create_task(probe_scheduler(factory, "secret", 1))

    await asyncio.sleep(0.1)
    assert not task.done()

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert task.done()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_probe_scheduler_handles_errors(db_session: AsyncSession) -> None:
    """Планировщик не падает при ошибке probe — продолжает цикл."""
    from app.db.models import Provider
    from app.db.workspace import ensure_default_workspace

    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    provider = Provider(
        workspace_id=ws_id,
        kind="ollama",
        base_url="http://stub:1234/v1",
        enabled=True,
        capabilities={},
    )
    db_session.add(provider)
    await db_session.commit()

    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    call_count = 0

    async def _failing_probe(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("probe failed")

    with patch("app.providers.probe_scheduler.probe_provider", new=_failing_probe):
        from app.providers.probe_scheduler import probe_scheduler

        task = asyncio.create_task(probe_scheduler(factory, "secret", 1))

        await asyncio.sleep(2.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert call_count >= 1
