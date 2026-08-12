"""CRUD для наборов оценки и импорт (T-224).

POST   /api/corpora/{corpus_id}/eval-sets       — создать набор с элементами
GET    /api/corpora/{corpus_id}/eval-sets       — список наборов корпуса
GET    /api/eval-sets/{id}                       — набор с элементами
DELETE /api/eval-sets/{id}                       — удалить набор
POST   /api/eval-sets/{id}/import                — импорт CodeSearchNet JSONL
POST   /api/eval-sets/{id}/runs                  — запуск прогона (T-225)
GET    /api/eval-sets/{id}/runs                  — список прогонов (T-225)
GET    /api/eval-runs/{id}                       — получить прогон (T-225)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.eval import (
    EvalImportResponse,
    EvalItemRead,
    EvalRunCreate,
    EvalRunListResponse,
    EvalRunRead,
    EvalSetCreateWithItems,
    EvalSetListResponse,
    EvalSetRead,
    EvalSetReadWithItems,
)
from app.auth.dependencies import current_user
from app.db.models import Corpus, EvalItem, EvalRun, EvalSet, IndexVersion, Model, Provider, User
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


# ---------------------------------------------------------------------------
# T-225: Прогон оценки
# ---------------------------------------------------------------------------


def _eval_run_to_read(run: EvalRun) -> EvalRunRead:
    return EvalRunRead(
        id=run.id,
        workspace_id=run.workspace_id,
        eval_set_id=run.eval_set_id,
        index_version_id=run.index_version_id,
        pipeline=run.pipeline,
        metrics=run.metrics,
        ts=run.ts,
    )


@router.post("/eval-sets/{eval_set_id}/runs", response_model=EvalRunRead)
async def create_eval_run(
    eval_set_id: str,
    body: EvalRunCreate,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EvalRunRead:
    """Запустить прогон оценки."""
    from app.rag.eval_runner import run_eval

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

    # Проверяем что index_version существует и принадлежит корпусу
    iv_result = await session.execute(
        select(IndexVersion).where(
            IndexVersion.id == body.index_version_id,
            IndexVersion.workspace_id == user.workspace_id,
        )
    )
    index_version = iv_result.scalar_one_or_none()
    if index_version is None:
        raise NotFound(f"Index version {body.index_version_id} not found")

    # Загружаем модель и провайдер для generate
    # Используем первую доступную модель в workspace
    model_result = await session.execute(
        select(Model)
        .where(Model.workspace_id == user.workspace_id, Model.enabled.is_(True))
        .limit(1)
    )
    model = model_result.scalar_one_or_none()
    if model is None:
        raise NotFound("No enabled model found in workspace")

    provider_result = await session.execute(
        select(Provider).where(Provider.id == model.provider_id)
    )
    provider = provider_result.scalar_one_or_none()
    if provider is None:
        raise NotFound(f"Provider for model {model.alias} not found")

    # Запускаем прогон
    eval_run = await run_eval(
        session=session,
        workspace_id=user.workspace_id,
        eval_set_id=eval_set_id,
        index_version_id=body.index_version_id,
        settings=request.app.state.settings,
        vector_store=request.app.state.vector_store,
        embedding_backend=request.app.state.embedding_backend,
        secret_key=request.app.state.secret_key,
        model=model,
        provider=provider,
        steps=body.steps,
    )
    await session.commit()

    return _eval_run_to_read(eval_run)


@router.get("/eval-sets/{eval_set_id}/runs", response_model=EvalRunListResponse)
async def list_eval_runs(
    eval_set_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EvalRunListResponse:
    """Список прогонов набора."""
    result = await session.execute(
        select(EvalRun)
        .where(
            EvalRun.eval_set_id == eval_set_id,
            EvalRun.workspace_id == user.workspace_id,
        )
        .order_by(EvalRun.ts.desc())
    )
    runs = result.scalars().all()
    return EvalRunListResponse(items=[_eval_run_to_read(r) for r in runs])


@router.get("/eval-runs/{run_id}", response_model=EvalRunRead)
async def get_eval_run(
    run_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EvalRunRead:
    """Получить прогон с метриками."""
    result = await session.execute(
        select(EvalRun).where(
            EvalRun.id == run_id,
            EvalRun.workspace_id == user.workspace_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFound(f"Eval run {run_id} not found")

    return _eval_run_to_read(run)
