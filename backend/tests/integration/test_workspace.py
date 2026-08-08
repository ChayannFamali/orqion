"""Тест workspace: идемпотентное создание, доступность через app.state."""

from __future__ import annotations

import pytest
from app.db.models import Workspace
from app.db.workspace import ensure_default_workspace
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_workspace_created_on_first_start(
    db_session: AsyncSession,
) -> None:
    ws_id = await ensure_default_workspace(db_session)
    assert ws_id is not None

    result = await db_session.execute(select(Workspace))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].id == ws_id


@pytest.mark.asyncio
async def test_workspace_creation_is_idempotent(
    db_session: AsyncSession,
) -> None:
    id1 = await ensure_default_workspace(db_session)
    await db_session.flush()
    id2 = await ensure_default_workspace(db_session)
    assert id1 == id2

    result = await db_session.execute(select(Workspace))
    rows = result.scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_workspace_id_available_in_app_state(
    app_fixture: FastAPI,
) -> None:
    assert app_fixture.state.workspace_id is not None
    assert len(app_fixture.state.workspace_id) == 36
