"""POST /api/corpora/{corpus_id}/documents, GET /api/corpora/{corpus_id}/documents.

Загрузка документов в корпус (T-204).
Доступ — через capability upload, не через role.name (§5.2).
Оригинал сохраняется в BlobStore до разбора (ADR-7).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.document import (
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentResponse,
)
from app.auth.dependencies import current_user
from app.config import Settings
from app.db.models import Document, User
from app.db.session import get_session
from app.errors import NotFound, OrqionError
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy
from app.rag.service import get_document, list_documents, upload_document

router = APIRouter(
    prefix="/api/corpora",
    tags=["documents"],
    dependencies=[Depends(current_user)],
)

_READ_CHUNK = 64 * 1024  # 64 KB


class UploadPermissionDenied(OrqionError):
    error_code = "upload_permission_denied"
    reason = "Нет прав для загрузки документов"
    status_code = 403
    hint = "Требуется capability upload"


async def _check_upload_capability(
    session: AsyncSession,
    user: User,
) -> None:
    """Проверяет capability upload через resolve_policy."""
    policy = await resolve_policy(session, user)
    if WILDCARD not in policy.capabilities and "upload" not in policy.capabilities:
        raise UploadPermissionDenied()


def _to_response(doc: Document) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        corpus_id=doc.corpus_id,
        filename=doc.filename,
        mime=doc.mime,
        sha256=doc.sha256,
        blob_uri=doc.blob_uri,
        source_type=doc.source_type,
        status=doc.status,
        uploaded_at=doc.uploaded_at,
    )


async def _file_to_async_iterator(file: UploadFile) -> AsyncIterator[bytes]:
    """Оборачивает UploadFile в AsyncIterator[bytes] для BlobStore.put.

    Читает чанками по _READ_CHUNK, не загружая весь файл в память.
    Подсчёт размера и лимит — в service._SizedIterator.
    """
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        yield chunk


@router.post("/{corpus_id}/documents", response_model=DocumentResponse, status_code=201)
async def upload_document_endpoint(
    corpus_id: str,
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> DocumentResponse:
    """Загрузка документа в корпус.

    Оригинал сохраняется в BlobStore (ADR-7), затем создаётся Document(status=pending).
    Дубликат определяется по (corpus_id, sha256).
    """
    await _check_upload_capability(session, user)

    settings = Settings()
    max_size_bytes = settings.max_upload_size_mb * 1024 * 1024
    allowed_extensions = [
        ext.strip() for ext in settings.allowed_upload_extensions.split(",") if ext.strip()
    ]

    blob_store = request.app.state.blob_store

    result = await upload_document(
        session,
        blob_store,
        workspace_id=request.app.state.workspace_id,
        corpus_id=corpus_id,
        filename=file.filename or "unknown",
        mime=file.content_type or "application/octet-stream",
        content=_file_to_async_iterator(file),
        max_size_bytes=max_size_bytes,
        allowed_extensions=allowed_extensions,
    )

    return _to_response(result.document)


@router.get("/{corpus_id}/documents", response_model=DocumentListResponse)
async def list_documents_endpoint(
    corpus_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> DocumentListResponse:
    """Список документов корпуса."""
    await _check_upload_capability(session, user)

    documents = await list_documents(
        session,
        workspace_id=request.app.state.workspace_id,
        corpus_id=corpus_id,
    )

    return DocumentListResponse(
        documents=[_to_response(d) for d in documents],
        total=len(documents),
    )


document_router = APIRouter(
    prefix="/api/documents",
    tags=["documents"],
    dependencies=[Depends(current_user)],
)


def _to_detail_response(doc: Document) -> DocumentDetailResponse:
    return DocumentDetailResponse(
        id=doc.id,
        corpus_id=doc.corpus_id,
        filename=doc.filename,
        mime=doc.mime,
        sha256=doc.sha256,
        source_type=doc.source_type,
        status=doc.status,
        uploaded_at=doc.uploaded_at,
    )


@document_router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document_endpoint(
    document_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> DocumentDetailResponse:
    """Метаданные документа по ID (T-306).

    Не возвращает blob_uri — это внутренний идентификатор хранения.
    """
    document = await get_document(
        session,
        workspace_id=user.workspace_id,
        document_id=document_id,
    )
    return _to_detail_response(document)


@document_router.get("/{document_id}/content")
async def get_document_content_endpoint(
    document_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> StreamingResponse:
    """Потоковая отдача содержимого документа через BlobStore (T-306).

    Оригинал читается через абстракцию BlobStore (ADR-7),
    не отдаёт blob_uri напрямую.
    """
    document = await get_document(
        session,
        workspace_id=user.workspace_id,
        document_id=document_id,
    )

    blob_store = request.app.state.blob_store

    if not await blob_store.exists(document.blob_uri):
        raise NotFound(
            constraint={"object": "blob", "uri": document.blob_uri},
            hint="Оригинал документа не найден в хранилище",
        )

    async def content_iterator() -> AsyncIterator[bytes]:
        try:
            async for chunk in blob_store.get(document.blob_uri):
                yield chunk
        except KeyError:
            raise NotFound(
                constraint={"object": "blob", "uri": document.blob_uri},
                hint="Оригинал документа не найден в хранилище",
            ) from None

    return StreamingResponse(
        content_iterator(),
        media_type=document.mime,
        headers={
            "Content-Disposition": f'inline; filename="{document.filename}"',
        },
    )
