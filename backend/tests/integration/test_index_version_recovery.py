"""T-431: Crash-recovery для index_version в статусе building.

При старте lifespan проверяет index_version со статусом "building" старше
настраиваемого порога → переводит в "interrupted". Активная версия корпуса
не затронута.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.db.models import Corpus, IndexVersion
from fastapi import FastAPI
from sqlalchemy import select


@pytest.mark.asyncio
async def test_stale_building_index_version_recovered(app_fixture: FastAPI) -> None:
    """Версия в "building" старше порога → "interrupted" после recovery.

    Симулируем crash: создаём index_version со status="building" и
    created_at в прошлом (старше 5 минут). Вызываем recovery —
    как если бы процесс рестартовал.
    """
    workspace_id = app_fixture.state.workspace_id
    settings = app_fixture.state.settings
    session_factory = app_fixture.state.db_session_factory

    async with session_factory() as session:
        corpus = Corpus(name="t431-corpus", workspace_id=workspace_id)
        session.add(corpus)
        await session.flush()

        stale_time = datetime.now(UTC) - timedelta(
            minutes=settings.index_building_stale_minutes + 10
        )
        stale_version = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="test-model",
            chunker="mixed-v1",
            chunker_version="1.0",
            status="building",
        )
        session.add(stale_version)
        await session.flush()
        # manually set created_at to the past (TimestampMixin uses server_default)
        from sqlalchemy import update as sa_update

        await session.execute(
            sa_update(IndexVersion)
            .where(IndexVersion.id == stale_version.id)
            .values(created_at=stale_time)
        )
        stale_version_id = stale_version.id
        await session.commit()

    from app.main import recover_stale_building_versions

    await recover_stale_building_versions(session_factory, workspace_id, settings)

    async with session_factory() as session:
        result = await session.execute(
            select(IndexVersion).where(IndexVersion.id == stale_version_id)
        )
        version = result.scalar_one()
        assert version.status == "interrupted", f"Expected 'interrupted', got '{version.status}'"


@pytest.mark.asyncio
async def test_recent_building_index_version_not_touched(app_fixture: FastAPI) -> None:
    """Версия в "building" младше порога — не тронута.

    Порог — defensive margin: свежие building могут быть легитимно
    активными (не на single-process, но §14.2).
    """
    workspace_id = app_fixture.state.workspace_id
    settings = app_fixture.state.settings
    session_factory = app_fixture.state.db_session_factory

    async with session_factory() as session:
        corpus = Corpus(name="t431-recent", workspace_id=workspace_id)
        session.add(corpus)
        await session.flush()

        recent_version = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="test-model",
            chunker="mixed-v1",
            chunker_version="1.0",
            status="building",
        )
        session.add(recent_version)
        await session.flush()
        recent_version_id = recent_version.id
        await session.commit()

    from app.main import recover_stale_building_versions

    await recover_stale_building_versions(session_factory, workspace_id, settings)

    async with session_factory() as session:
        result = await session.execute(
            select(IndexVersion).where(IndexVersion.id == recent_version_id)
        )
        version = result.scalar_one()
        assert version.status == "building", (
            f"Expected 'building' (not yet stale), got '{version.status}'"
        )


@pytest.mark.asyncio
async def test_active_version_not_affected_by_recovery(app_fixture: FastAPI) -> None:
    """Recovery не трогает активную версию корпуса (status=active)."""
    workspace_id = app_fixture.state.workspace_id
    settings = app_fixture.state.settings
    session_factory = app_fixture.state.db_session_factory

    async with session_factory() as session:
        corpus = Corpus(name="t431-active", workspace_id=workspace_id)
        session.add(corpus)
        await session.flush()

        active_version = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="test-model",
            chunker="mixed-v1",
            chunker_version="1.0",
            status="active",
        )
        session.add(active_version)
        await session.flush()
        corpus.active_index_version_id = active_version.id
        await session.commit()
        active_version_id = active_version.id

    from app.main import recover_stale_building_versions

    await recover_stale_building_versions(session_factory, workspace_id, settings)

    async with session_factory() as session:
        result = await session.execute(
            select(IndexVersion).where(IndexVersion.id == active_version_id)
        )
        version = result.scalar_one()
        assert version.status == "active"
