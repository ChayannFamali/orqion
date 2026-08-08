"""SQLAlchemy-модели всех таблиц. workspace_id присутствует в каждой таблице."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, WorkspaceMixin


class Workspace(Base, IdMixin, TimestampMixin):
    """Единственный workspace экземпляра (ADR-3)."""

    __tablename__ = "workspace"

    name: Mapped[str] = mapped_column(String(255), nullable=False)


class Role(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Роль: name, is_builtin, policy (JSON). Источник правды для resolve_policy."""

    __tablename__ = "role"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    policy: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)


class User(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Пользователь: email, password_hash, role_id, is_active."""

    __tablename__ = "user"
    __table_args__ = (UniqueConstraint("workspace_id", "email", name="uq_user_workspace_email"),)

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("role.id"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Session(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Сессия: user_id, expires_at. Инвалидируется при выходе."""

    __tablename__ = "session"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
