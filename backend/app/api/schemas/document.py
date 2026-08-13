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
    source_type: str
    status: str
    error: str | None = None
    uploaded_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
