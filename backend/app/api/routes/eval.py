"""CRUD для наборов оценки и импорт (T-224).

POST   /api/corpora/{corpus_id}/eval-sets       — создать набор с элементами
GET    /api/corpora/{corpus_id}/eval-sets       — список наборов корпуса
GET    /api/eval-sets/{id}                       — набор с элементами
DELETE /api/eval-sets/{id}                       — удалить набор
POST   /api/eval-sets/{id}/import                — импорт CodeSearchNet JSONL
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.eval import (
    EvalImportResponse,
    EvalItemRead,
    EvalSetCreateWithItems,
    EvalSetListResponse,
    EvalSetRead,
    EvalSetReadWithItems,
)
from app.auth.dependencies import current_user
from app.db.models import Corpus, EvalItem, EvalSet, User
from app.db.session import get_session
from app.errors import NotFound
from app.rag.eval_import import ImportItem, import_eval_set

router = APIRouter(
    prefix="/api",
    tags=["eval"],
    dependencies=[Depends(current_user)],
)


def _eval_set_to_read(es: EvalSet) -> EvalSetRead:
    return EvalSetRead(
        id=es.id,
        workspace_id=es.workspace_id,
        corpus_id=es.corpus_id,
        name=es.name,
        created_at=es.created_at,
    )


def _eval_item_to_read(item: EvalItem) -> EvalItemRead:
    return EvalItemRead(
        id=item.id,
        workspace_id=item.workspace_id,
        eval_set_id=item.eval_set_id,
        question=item.question,
        expected_doc_ids=item.expected_doc_ids,
        expected_answer=item.expected_answer,
    )


async def _get_corpus_or_404(session: AsyncSession, corpus_id: str, workspace_id: str) -> Corpus:
    result = await session.execute(
        select(Corpus).where(Corpus.id == corpus_id, Corpus.workspace_id == workspace_id)
    )
    corpus = result.scalar_one_or_none()
    if corpus is None:
        raise NotFound(f"Corpus {corpus_id} not found")
    return corpus


@router.post("/corpora/{corpus_id}/eval-sets", response_model=EvalSetReadWithItems)
async def create_eval_set(
    corpus_id: str,
    body: EvalSetCreateWithItems,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EvalSetReadWithItems:
    """Создать набор оценки с элементами."""
    await _get_corpus_or_404(session, corpus_id, user.workspace_id)

    items = [
        ImportItem(
            question=i.question,
            expected_doc_ids=i.expected_doc_ids,
            expected_answer=i.expected_answer,
        )
        for i in body.items
    ]

    try:
        eval_set = await import_eval_set(
            session,
            user.workspace_id,
            corpus_id,
            body.name,
            items,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise NotFound("Eval set name already exists in this workspace") from None

    # Явная загрузка items (lazy loading не работает в async)
    item_result = await session.execute(select(EvalItem).where(EvalItem.eval_set_id == eval_set.id))
    db_items = list(item_result.scalars().all())
    return EvalSetReadWithItems(
        id=eval_set.id,
        workspace_id=eval_set.workspace_id,
        corpus_id=eval_set.corpus_id,
        name=eval_set.name,
        created_at=eval_set.created_at,
        items=[_eval_item_to_read(i) for i in db_items],
    )


@router.get("/corpora/{corpus_id}/eval-sets", response_model=EvalSetListResponse)
async def list_eval_sets(
    corpus_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EvalSetListResponse:
    """Список наборов оценки корпуса."""
    await _get_corpus_or_404(session, corpus_id, user.workspace_id)

    result = await session.execute(
        select(EvalSet)
        .where(
            EvalSet.corpus_id == corpus_id,
            EvalSet.workspace_id == user.workspace_id,
        )
        .order_by(EvalSet.created_at.desc())
    )
    sets = result.scalars().all()
    return EvalSetListResponse(items=[_eval_set_to_read(s) for s in sets])


@router.get("/eval-sets/{eval_set_id}", response_model=EvalSetReadWithItems)
async def get_eval_set(
    eval_set_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EvalSetReadWithItems:
    """Получить набор с элементами."""
    result = await session.execute(
        select(EvalSet)
        .where(
            EvalSet.id == eval_set_id,
            EvalSet.workspace_id == user.workspace_id,
        )
        .options(selectinload(EvalSet.items))
    )
    eval_set = result.scalar_one_or_none()
    if eval_set is None:
        raise NotFound(f"Eval set {eval_set_id} not found")

    return EvalSetReadWithItems(
        id=eval_set.id,
        workspace_id=eval_set.workspace_id,
        corpus_id=eval_set.corpus_id,
        name=eval_set.name,
        created_at=eval_set.created_at,
        items=[_eval_item_to_read(i) for i in sorted(eval_set.items, key=lambda x: x.id)],
    )


@router.delete("/eval-sets/{eval_set_id}", status_code=204)
async def delete_eval_set(
    eval_set_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Удалить набор оценки (каскадно удаляет элементы и прогоны)."""
    result = await session.execute(
        select(EvalSet).where(
            EvalSet.id == eval_set_id,
            EvalSet.workspace_id == user.workspace_id,
        )
    )
    eval_set = result.scalar_one_or_none()
    if eval_set is None:
        raise NotFound(f"Eval set {eval_set_id} not found")

    await session.delete(eval_set)
    await session.commit()


@router.post("/eval-sets/{eval_set_id}/import", response_model=EvalImportResponse)
async def import_to_eval_set(
    eval_set_id: str,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EvalImportResponse:
    """Импорт CodeSearchNet JSONL в существующий eval_set.

    Файл парсится in-memory, не сохраняется на диск.
    """
    # Проверяем что eval_set существует
    result = await session.execute(
        select(EvalSet).where(
            EvalSet.id == eval_set_id,
            EvalSet.workspace_id == user.workspace_id,
        )
    )
    eval_set = result.scalar_one_or_none()
    if eval_set is None:
        raise NotFound(f"Eval set {eval_set_id} not found")

    # Читаем файл во временный файл (нужен path для parse_codesearchnet)
    content = await file.read()
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        from app.rag.eval_import import parse_codesearchnet, resolve_doc_ids

        entries = parse_codesearchnet(tmp_path)
        doc_id_map = await resolve_doc_ids(session, eval_set.corpus_id, entries)

        items_to_add: list[ImportItem] = []
        matched = 0
        for entry in entries:
            doc_ids = doc_id_map.get(entry.filepath, [])
            if doc_ids:
                matched += 1
            items_to_add.append(
                ImportItem(
                    question=entry.question,
                    expected_doc_ids=doc_ids,
                    expected_answer=entry.func_code,
                )
            )

        # Добавляем items в существующий набор
        for item in items_to_add:
            new_item = EvalItem(
                workspace_id=user.workspace_id,
                eval_set_id=eval_set_id,
                question=item.question,
                expected_doc_ids=item.expected_doc_ids,
                expected_answer=item.expected_answer,
            )
            session.add(new_item)

        await session.commit()
    finally:
        tmp_path.unlink(missing_ok=True)

    # Подсчитаем total items
    count_result = await session.execute(
        select(EvalItem).where(EvalItem.eval_set_id == eval_set_id)
    )
    total = len(list(count_result.scalars().all()))

    return EvalImportResponse(
        eval_set_id=eval_set_id,
        total_items=total,
        matched_items=matched,
    )
