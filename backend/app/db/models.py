"""SQLAlchemy-модели всех таблиц. workspace_id присутствует в каждой таблице."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class Workspace(Base, IdMixin, TimestampMixin):
    """Единственный workspace экземпляра (ADR-3)."""

    __tablename__ = "workspace"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
