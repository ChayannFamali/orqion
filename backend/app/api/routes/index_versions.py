"""Управление версиями индекса корпуса (T-314).

POST   /api/corpora/{corpus_id}/index-versions           — запуск сборки (202)
GET    /api/corpora/{corpus_id}/index-versions           — список версий
GET    /api/corpora/{corpus_id}/index-versions/{id}      — детали версии (progress)
POST   /api/corpora/{corpus_id}/index-versions/{id}/activate  — активация
POST   /api/corpora/{corpus_id}/index-versions/rollback   — откат
POST   /api/corpora/{corpus_id}/index-versions/cleanup    — удаление retired

Доступ: manage_corpora (architect, admin) → 404 для остальных.
Сборка запускается как background task (asyncio.create_task).
Прогресс поллится через GET /{id} — читает index_version.stats.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.index_version import (
    ActivateResponse,
    BuildResponse,
    CleanupResponse,
    IndexVersionListResponse,
    IndexVersionResponse,
    RollbackResponse,
)
from app.auth.dependencies import current_user
from app.db.models import Corpus, EvalRun, IndexVersion, User
from app.db.session import get_session
from app.errors import BadRequest, NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy
from app.rag.index_builder import build_index_version
from app.rag.service import (
    activate_index_version,
    cleanup_retired_versions,
    rollback_index_version,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/corpora",
    tags=["index-versions"],
    dependencies=[Depends(current_user)],
)


async def _check_manage_corpora(session: AsyncSession, user: User) -> None:
    """Проверяет manage_corpora capability. Raises NotFound (404) если нет."""
    policy = await resolve_policy(session, user)
    if WILDCARD in policy.capabilities or "manage_corpora" in policy.capabilities:
        return
    raise NotFound(
        constraint={"object": "index-versions"},
        hint="Недостаточно прав для управления версиями индекса",
    )


async def _load_corpus(session: AsyncSession, corpus_id: str, workspace_id: str) -> Corpus:
    corpus = await session.get(Corpus, corpus_id)
    if corpus is None or corpus.workspace_id != workspace_id:
        raise NotFound(
            constraint={"object": "corpus", "id": corpus_id},
            hint="Корпус не найден",
        )
    return corpus


def _to_response(iv: IndexVersion) -> IndexVersionResponse:
    return IndexVersionResponse(
        id=iv.id,
        corpus_id=iv.corpus_id,
        embedding_model=iv.embedding_model,
        chunker=iv.chunker,
        chunker_version=iv.chunker_version,
        status=iv.status,
        stats=iv.stats,
        created_at=iv.created_at,
    )


@router.post(
    "/{corpus_id}/index-versions",
    response_model=BuildResponse,
    status_code=202,
)
async def build_index_endpoint(
    corpus_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> BuildResponse:
    """Запуск сборки версии индекса (background task).

    Возвращает 202 с index_version_id. Прогресс поллится через GET /{id}.
    Embedding model и chunker не передаются в body — backend использует
    единственный настроенный backend (app.state.embedding_backend).
    """
    workspace_id = request.app.state.workspace_id
    await _check_manage_corpora(session, user)
    await _load_corpus(session, corpus_id, workspace_id)

    blob_store = request.app.state.blob_store
    vector_store = request.app.state.vector_store
    embedding_backend = request.app.state.embedding_backend
    session_factory = request.app.state.db_session_factory

    # Создаём index_version в отдельной сессии, чтобы получить ID
    # до запуска background task.
    async with session_factory() as build_session:
        result = await build_index_version(
            build_session,
            blob_store,
            vector_store,
            embedding_backend,
            workspace_id=workspace_id,
            corpus_id=corpus_id,
        )
        iv = await build_session.get(IndexVersion, result.index_version_id)
        iv_status = iv.status if iv is not None else "building"
        await build_session.commit()

    index_version_id = result.index_version_id

    return BuildResponse(index_version_id=index_version_id, status=iv_status)


@router.get(
    "/{corpus_id}/index-versions",
    response_model=IndexVersionListResponse,
)
async def list_index_versions_endpoint(
    corpus_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> IndexVersionListResponse:
    """Список версий индекса корпуса."""
    workspace_id = request.app.state.workspace_id
    await _check_manage_corpora(session, user)
    await _load_corpus(session, corpus_id, workspace_id)

    result = await session.execute(
        select(IndexVersion)
        .where(
            IndexVersion.workspace_id == workspace_id,
            IndexVersion.corpus_id == corpus_id,
        )
        .order_by(IndexVersion.created_at.desc())
    )
    versions = list(result.scalars().all())

    return IndexVersionListResponse(
        versions=[_to_response(iv) for iv in versions],
        total=len(versions),
    )


@router.get(
    "/{corpus_id}/index-versions/{version_id}",
    response_model=IndexVersionResponse,
)
async def get_index_version_endpoint(
    corpus_id: str,
    version_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> IndexVersionResponse:
    """Детали версии индекса (прогресс сборки)."""
    workspace_id = request.app.state.workspace_id
    await _check_manage_corpora(session, user)
    await _load_corpus(session, corpus_id, workspace_id)

    iv = await session.get(IndexVersion, version_id)
    if iv is None or iv.workspace_id != workspace_id or iv.corpus_id != corpus_id:
        raise NotFound(
            constraint={"object": "index_version", "id": version_id},
            hint="Версия индекса не найдена",
        )

    return _to_response(iv)


@router.post(
    "/{corpus_id}/index-versions/{version_id}/activate",
    response_model=ActivateResponse,
)
async def activate_index_version_endpoint(
    corpus_id: str,
    version_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> ActivateResponse:
    """Активация версии индекса (ADR-8 blue-green).

    Возвращает warning если для версии нет успешного прогона оценки.
    """
    workspace_id = request.app.state.workspace_id
    await _check_manage_corpora(session, user)
    await _load_corpus(session, corpus_id, workspace_id)

    previous_version_id = await activate_index_version(
        session,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        new_version_id=version_id,
        actor_user_id=user.id,
    )

    # Проверка: есть ли успешный прогон оценки для этой версии
    has_eval = await session.execute(
        select(
            exists().where(
                EvalRun.index_version_id == version_id,
                EvalRun.metrics.is_not(None),
            )
        )
    )
    warning = None if has_eval.scalar() else "Нет успешного прогона оценки для этой версии индекса"

    await session.commit()

    return ActivateResponse(
        active_version_id=version_id,
        previous_version_id=previous_version_id,
        warning=warning,
    )


@router.post(
    "/{corpus_id}/index-versions/rollback",
    response_model=RollbackResponse,
)
async def rollback_index_version_endpoint(
    corpus_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> RollbackResponse:
    """Откат к предыдущей версии индекса (ADR-8)."""
    workspace_id = request.app.state.workspace_id
    await _check_manage_corpora(session, user)
    await _load_corpus(session, corpus_id, workspace_id)

    restored_version_id = await rollback_index_version(
        session,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        actor_user_id=user.id,
    )

    if restored_version_id is None:
        raise BadRequest(
            "Откатывать некуда — нет предыдущей активной версии",
            constraint={"corpus_id": corpus_id},
            hint="Активируйте версию индекса перед откатом",
        )

    await session.commit()

    return RollbackResponse(
        active_version_id=restored_version_id,
    )


@router.post(
    "/{corpus_id}/index-versions/cleanup",
    response_model=CleanupResponse,
)
async def cleanup_retired_versions_endpoint(
    corpus_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> CleanupResponse:
    """Удаление retired-версий индекса (chunks + vectors + index_version)."""
    workspace_id = request.app.state.workspace_id
    await _check_manage_corpora(session, user)
    await _load_corpus(session, corpus_id, workspace_id)

    vector_store = request.app.state.vector_store

    deleted_count = await cleanup_retired_versions(
        session,
        vector_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        actor_user_id=user.id,
    )

    await session.commit()

    return CleanupResponse(deleted_count=deleted_count)
