"""Схемы API для журнала аудита (T-317)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: str
    ts: datetime
    actor_user_id: str
    action: str
    object_type: str
    object_id: str | None = None
    meta: dict[str, object] = {}


class AuditLogListResponse(BaseModel):
    entries: list[AuditLogResponse]
    total: int


class AuditActionsResponse(BaseModel):
    actions: list[str]
