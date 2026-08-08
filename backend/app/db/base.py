"""Базовый класс моделей: id (UUID), created_at, WorkspaceMixin."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Декларативный базовый класс SQLAlchemy 2.0."""


class IdMixin:
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_uuid,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )


class WorkspaceMixin:
    """workspace_id присутствует во всех таблицах кроме workspace (ADR-3)."""

    workspace_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
