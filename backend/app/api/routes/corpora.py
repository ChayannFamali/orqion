"""GET /api/corpora, POST /api/corpora.

Access control: manage_corpora capability (architect + admin via *).
Non-admin/non-architect → 404 (прецедент T-308/T-310/T-311).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.corpus import (
    CorpusCreate,
    CorpusListResponse,
    CorpusResponse,
)
from app.auth.dependencies import current_user
from app.db.models import Corpus, User
from app.db.session import get_session
from app.errors import BadRequest, NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/corpora",
    tags=["corpora"],
    dependencies=[Depends(current_user)],
)


async def _check_manage_corpora(session: AsyncSession, user: User) -> bool:
    """True если manage_corpora или * в capabilities."""
    policy = await resolve_policy(session, user)
    if WILDCARD in policy.capabilities:
        return True
    return "manage_corpora" in policy.capabilities


def _to_response(corpus: Corpus) -> CorpusResponse:
    return CorpusResponse(
        id=corpus.id,
        name=corpus.name,
        data_class=corpus.data_class,
        pinned_model_id=corpus.pinned_model_id,
        active_index_version_id=corpus.active_index_version_id,
    )


@router.get("", response_model=CorpusListResponse)
async def list_corpora(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> CorpusListResponse:
    if not await _check_manage_corpora(session, user):
        raise NotFound(
            constraint={"object": "corpora", "reason": "manage_corpora required"},
            hint="Нет права на управление корпусами",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Corpus).where(Corpus.workspace_id == workspace_id).order_by(Corpus.created_at.desc())
    )
    corpora = result.scalars().all()
    return CorpusListResponse(corpora=[_to_response(c) for c in corpora])


@router.post("", response_model=CorpusResponse, status_code=201)
async def create_corpus(
    body: CorpusCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> CorpusResponse:
    if not await _check_manage_corpora(session, user):
        raise NotFound(
            constraint={"object": "corpora", "reason": "manage_corpora required"},
            hint="Нет права на управление корпусами",
        )

    workspace_id = request.app.state.workspace_id

    # Проверка дубликата имени до insert
    existing = await session.execute(
        select(Corpus).where(
            Corpus.workspace_id == workspace_id,
            Corpus.name == body.name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise BadRequest(
            "Имя корпуса должно быть уникально в рамках workspace",
            hint=f"Имя '{body.name}' уже существует",
        )

    corpus = Corpus(
        workspace_id=workspace_id,
        name=body.name,
        data_class=body.data_class,
        pinned_model_id=body.pinned_model_id,
    )
    session.add(corpus)

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise BadRequest(
            "Имя корпуса должно быть уникально в рамках workspace",
            hint=f"Имя '{body.name}' уже существует",
        )

    await session.commit()
    await session.refresh(corpus)

    return _to_response(corpus)
