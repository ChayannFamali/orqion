"""GET /api/corpora, POST /api/corpora.

Access control: manage_corpora capability (architect + admin via *).
Non-admin/non-architect → 404 (прецедент T-308/T-310/T-311).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.corpus import (
    AvailableCorporaResponse,
    AvailableCorpusEntry,
    CorpusCreate,
    CorpusDeleteResponse,
    CorpusListResponse,
    CorpusResponse,
    CorpusUpdate,
)
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.db.models import (
    Chunk,
    Corpus,
    Document,
    EvalItem,
    EvalRun,
    EvalSet,
    IndexVersion,
    User,
)
from app.db.session import get_session
from app.errors import BadRequest, NotFound
from app.policy.enforce import _matches
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy
from app.rag.blob import BlobStore
from app.rag.vector_store import VectorStore

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


@router.get("/available", response_model=AvailableCorporaResponse)
async def available_corpora(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> AvailableCorporaResponse:
    """Корпуса, доступные пользователю для чата (T-439).

    Видимость — по policy.corpora роли (та же семантика, что у проверки
    в enforce: шаблоны имён, «*» = все). Управление корпусами
    (manage_corpora) НЕ требуется — это чтение для селектора чата.

    Формат ответа — имя корпуса (решение Г1: идентификация по имени,
    как в policy.corpora; второй способ адресации не вводится).
    """
    policy = await resolve_policy(session, user)
    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Corpus).where(Corpus.workspace_id == workspace_id).order_by(Corpus.created_at.desc())
    )
    corpora = result.scalars().all()
    entries = [
        AvailableCorpusEntry(
            id=corpus.id,
            name=corpus.name,
            data_class=corpus.data_class,
            ready=corpus.active_index_version_id is not None,
        )
        for corpus in corpora
        if _matches(policy.corpora, corpus.name)
    ]
    return AvailableCorporaResponse(corpora=entries)


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


@router.patch("/{corpus_id}", response_model=CorpusResponse)
async def update_corpus(
    corpus_id: str,
    body: CorpusUpdate,
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

    result = await session.execute(
        select(Corpus).where(
            Corpus.workspace_id == workspace_id,
            Corpus.id == corpus_id,
        )
    )
    corpus = result.scalar_one_or_none()
    if corpus is None:
        raise NotFound(
            constraint={"object": "corpus", "id": corpus_id},
            hint="Корпус не найден в workspace",
        )

    if body.data_class is not None and body.data_class != corpus.data_class:
        old_data_class = corpus.data_class
        corpus.data_class = body.data_class

        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_user_id=user.id,
            action="corpus.data_class_changed",
            object_type="corpus",
            object_id=corpus.id,
            meta={
                "old": old_data_class,
                "new": body.data_class,
                "corpus_name": corpus.name,
            },
        )

    await session.commit()
    await session.refresh(corpus)

    return _to_response(corpus)


@router.delete("/{corpus_id}", response_model=CorpusDeleteResponse)
async def delete_corpus(
    corpus_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> CorpusDeleteResponse:
    """Удаление корпуса со всем содержимым.

    Удаляет документы, все версии индекса с чанками и векторами, наборы
    оценки с вопросами и их прогонами. Файлы документов удаляются, только
    если на них не ссылаются документы других корпусов.
    """
    if not await _check_manage_corpora(session, user):
        raise NotFound(
            constraint={"object": "corpora", "reason": "manage_corpora required"},
            hint="Нет права на управление корпусами",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Corpus).where(
            Corpus.workspace_id == workspace_id,
            Corpus.id == corpus_id,
        )
    )
    corpus = result.scalar_one_or_none()
    if corpus is None:
        raise NotFound(
            constraint={"object": "corpus", "id": corpus_id},
            hint="Корпус не найден в workspace",
        )

    # Собираем ссылки до удаления строк.
    doc_result = await session.execute(
        select(Document.blob_uri).where(Document.corpus_id == corpus_id)
    )
    blob_uris = {uri for uri in doc_result.scalars().all()}

    version_result = await session.execute(
        select(IndexVersion.id).where(IndexVersion.corpus_id == corpus_id)
    )
    version_ids = list(version_result.scalars().all())

    eval_set_result = await session.execute(
        select(EvalSet.id).where(EvalSet.corpus_id == corpus_id)
    )
    eval_set_ids = list(eval_set_result.scalars().all())

    documents_count = (
        await session.scalar(select(func.count(Document.id)).where(Document.corpus_id == corpus_id))
    ) or 0

    # Векторы удаляются до строк версий (хранилище адресует по версии).
    vector_store: VectorStore = request.app.state.vector_store
    for version_id in version_ids:
        await vector_store.drop_version(version_id)

    # Строки — снизу вверх по связям.
    if version_ids:
        await session.execute(delete(Chunk).where(Chunk.index_version_id.in_(version_ids)))
        await session.execute(delete(IndexVersion).where(IndexVersion.corpus_id == corpus_id))
    if eval_set_ids:
        await session.execute(delete(EvalRun).where(EvalRun.eval_set_id.in_(eval_set_ids)))
        await session.execute(delete(EvalItem).where(EvalItem.eval_set_id.in_(eval_set_ids)))
        await session.execute(delete(EvalSet).where(EvalSet.corpus_id == corpus_id))
    await session.execute(delete(Document).where(Document.corpus_id == corpus_id))

    # Файлы — только без ссылок из других корпусов.
    blob_store: BlobStore = request.app.state.blob_store
    blobs_deleted = 0
    for uri in blob_uris:
        other_refs = await session.scalar(
            select(func.count(Document.id)).where(
                Document.blob_uri == uri,
                Document.corpus_id != corpus_id,
            )
        )
        if not other_refs:
            await blob_store.delete(uri)
            blobs_deleted += 1

    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        action="corpus.delete",
        object_type="corpus",
        object_id=corpus.id,
        meta={
            "corpus_name": corpus.name,
            "documents": documents_count,
            "index_versions": len(version_ids),
            "eval_sets": len(eval_set_ids),
            "blobs_deleted": blobs_deleted,
        },
    )

    await session.delete(corpus)
    await session.commit()
    return CorpusDeleteResponse(deleted=True)
