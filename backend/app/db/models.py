"""SQLAlchemy-модели всех таблиц. workspace_id присутствует в каждой таблице."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class AuditLog(Base, IdMixin, WorkspaceMixin):
    """Журнал действий администратора. Append-only (arch.md §5.3)."""

    __tablename__ = "audit_log"

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    actor_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    meta: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)


class Provider(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Провайдер: kind, base_url, api_key_enc (AES-GCM), enabled, capabilities."""

    __tablename__ = "provider"

    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_enc: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capabilities: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    last_probe_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    models: Mapped[list[Model]] = relationship(back_populates="provider")


class Model(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Модель провайдера: alias, upstream_name, locality, лимиты, стоимость."""

    __tablename__ = "model"
    __table_args__ = (UniqueConstraint("workspace_id", "alias", name="uq_model_workspace_alias"),)

    provider_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("provider.id"),
        nullable=False,
    )
    provider: Mapped[Provider] = relationship(back_populates="models")
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    upstream_name: Mapped[str] = mapped_column(String(255), nullable=False)
    locality: Mapped[str] = mapped_column(String(20), nullable=False)
    max_input_tokens: Mapped[int | None] = mapped_column(nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(nullable=True)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cost_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_out: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RoutingRule(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Правило маршрутизации. arch.md §7.2, S-12.

    Поля when_* — условия срабатывания (None = не проверяется).
    to_models — список алиасов для сужения множества.
    allow_locality — фильтр по locality (local/external).
    fallback_models — резервные алиасы при недоступности провайдера.
    """

    __tablename__ = "routing_rule"

    order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    when_corpus_class: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    when_role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    when_task: Mapped[str | None] = mapped_column(String(100), nullable=True)
    when_model_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_models: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    allow_locality: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    fallback_models: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")


class Conversation(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Диалог: title, archived. Доступ только владельцу.

    Заголовок формируется по первому сообщению (arch.md §5.1).
    """

    __tablename__ = "conversation"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
    )


class Message(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Сообщение диалога: role, content, model_id, tokens, meta.

    arch.md §5.1: message(id, conversation_id, role, content, model_id,
    tokens_in, tokens_out, created_at, meta JSON).
    workspace_id — ADR-3, прямо в каждой таблице.
    """

    __tablename__ = "message"

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversation.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("model.id"),
        nullable=True,
    )
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")
