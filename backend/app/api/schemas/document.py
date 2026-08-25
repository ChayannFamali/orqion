"""Схемы запроса и ответа для document эндпоинтов (T-204)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: str
    corpus_id: str
    filename: str
    mime: str
    sha256: str
    blob_uri: str
    size_bytes: int | None = None
    source_type: str
    status: str
    error: str | None = None
    uploaded_at: datetime


class DocumentDetailResponse(BaseModel):
    """Метаданные документа без внутреннего blob_uri (T-306)."""

    id: str
    corpus_id: str
    filename: str
    mime: str
    sha256: str
    size_bytes: int | None = None
    source_type: str
    status: str
    error: str | None = None
    uploaded_at: datetime


class DocumentDeleteResponse(BaseModel):
    """Результат удаления документа (механизм отложенного удаления, BUG-020).

    deleted=True: физическое удаление выполнено.
    deleted=False: документ помечен на удаление (у него есть чанки в
    версиях индекса — целостность снапшотов по ADR-8 сохраняется); он
    исключается из будущих сборок, физически удаляется повторным
    вызовом, когда чанков не останется.
    """

    deleted: bool
    status: str
    reason: str | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
