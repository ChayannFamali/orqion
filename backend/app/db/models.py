"""SQLAlchemy-модели всех таблиц. workspace_id присутствует в каждой таблице."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin, WorkspaceMixin, _utcnow


class Workspace(Base, IdMixin, TimestampMixin):
    """Единственный workspace экземпляра (ADR-3)."""

    __tablename__ = "workspace"

    name: Mapped[str] = mapped_column(String(255), nullable=False)


class RagSettings(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Настройки RAG-поиска уровня рабочей области (Т-506).

    Одна строка на workspace (уникальность по workspace_id). Область
    действия — глобально, без привязки к корпусу; путь для будущего
    переопределения на корпус — отдельная таблица по образцу
    ``Corpus.pinned_model_id``, схема это допускает без переделки.

    ``relevance_threshold`` — проценты 0–100; 0 = сентинел «фильтр
    выключен» (шаг фильтрации не выполняется). Применяется к скорам
    реранкера (0–1), только когда реранкер реально отработал.
    ``max_fragments`` — ограничение сверху 1–8, срез после реранкера
    до токен-лимита сборки контекста.
    ``cluster_count`` — число групп графа связей документов 2–20
    (Т-505); задаёт администратор, автоподбор и автоназвания не
    предусмотрены.
    """

    __tablename__ = "rag_settings"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_rag_settings_workspace"),)

    relevance_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_fragments: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    cluster_count: Mapped[int] = mapped_column(Integer, nullable=False, default=8)


class PromptTemplate(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Личный сохранённый промпт пользователя (Т-507).

    Первая версия — только личные шаблоны: владелец ``user_id``, CRUD
    только у владельца. В схеме оставлен путь к общим шаблонам рабочей
    области (решение дизайн-ревью Т-507, по образцу Т-506): общий шаблон
    позже = ``user_id`` пусто + отдельное правило редактирования.

    ``title`` — до 200 символов (ограничение колонки). ``body`` — текст
    шаблона без плейсхолдеров; предельная длина проверяется по настройке
    ``prompt_template_max_chars``.
    """

    __tablename__ = "prompt_template"
    __table_args__ = (Index("ix_prompt_template_ws_user", "workspace_id", "user_id"),)

    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)


class Role(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Роль: name, is_builtin, policy (JSON). Источник правды для resolve_policy."""

    __tablename__ = "role"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    policy: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)


class Team(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Команда/подразделение для team-scoped аналитики (T-402a).

    Manager видит аналитику только по пользователям своей команды.
    Team CRUD не входит в T-402a — управляется через admin user management.
    """

    __tablename__ = "team"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_team_workspace_name"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False)


class User(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Пользователь: email, password_hash (nullable для OIDC), role_id, is_active.

    auth_method: "local" (password), "oidc" (external IdP), "mixed" (оба способа).
    external_subject/external_issuer — для OIDC-сопоставления (T-404).
    team_id — команда для manager-scoped аналитики (T-402a). Nullable: NULL = не в команде.
    ondelete="SET NULL" — удаление команды не блокируется пользователями.
    """

    __tablename__ = "user"
    __table_args__ = (UniqueConstraint("workspace_id", "email", name="uq_user_workspace_email"),)

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("role.id"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auth_method: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    external_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_issuer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(String(512), nullable=True)
    team_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("team.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class Session(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Сессия: user_id, expires_at. Инвалидируется при выходе.

    impersonated_by — ID родительской сессии админа при имперсонации.
    None для обычных сессий. Позволяет восстановить админскую сессию при выходе.
    """

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
    impersonated_by: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        default=None,
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
    # Т-445 (каркас): провайдер умеет включать/выключать режим рассуждения.
    # Ручной флаг по паттерну supports_reasoning; без него политика не знает,
    # для какой модели пробовать переключение.
    reasoning_toggleable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Т-502: пригодность модели к инструментам (агентный модуль). Ручной флаг
    # по образцу флагов рассуждения (решение 3 дизайн-ревью): администратор
    # ставит сам, автоопределение пробой не делается. Точка создания
    # агентного диалога видна только при наличии модели с этим флагом.
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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
    # Т-502: режим разговора — "chat" (обычный) или "agent" (агентный
    # диалог, цикл «модель → инструменты → модель»). Точка входа в
    # агентный модуль — отдельная карточка (решение 10 дизайн-ревью),
    # обычный чат поведение не меняет.
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="chat")
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    # T-442: маркер мягкого сброса контекста — сообщения до этой отметки
    # не входят в историю для модели; видимая лента диалога не меняется.
    context_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
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


class UsageEvent(Base, IdMixin, WorkspaceMixin, TimestampMixin):
    """Запись о расходе. arch.md §5.1.

    Содержимое запросов и ответов НЕ пишется (AGENTS.md §5.2, §14).
    conversation_id/message_id — nullable: при удалении диалога
    становятся NULL, запись о расходе сохраняется (T-115 пометка).
    """

    __tablename__ = "usage_event"

    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id"),
        nullable=True,
        index=True,
    )
    model_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("model.id"),
        nullable=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversation.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("message.id", ondelete="SET NULL"),
        nullable=True,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Trace(Base, IdMixin, WorkspaceMixin, TimestampMixin):
    """Трассировка запроса. arch.md §5.1, ADR-14.

    Один trace на чат-запрос. span — шаги конвейера.
    conversation_id/message_id — nullable FK, не каскадные (T-118 пометка).
    """

    __tablename__ = "trace"

    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversation.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("message.id", ondelete="SET NULL"),
        nullable=True,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")


class Span(Base, IdMixin, WorkspaceMixin, TimestampMixin):
    """Шаг конвейера. arch.md §5.1, ADR-14.

    payload JSON — тела шагов (промпты, чанки), отдельный срок хранения (§5.3).
    parent_id — иерархия span'ов (вложенные шаги).
    """

    __tablename__ = "span"

    trace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("trace.id"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("span.id"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)


class UsageDaily(Base, WorkspaceMixin):
    """Суточный rollup usage_event. arch.md §5.3, ADR-16.

    Идемпотентен: повторный пересчёт за день заменяет строки, не удваивает.
    PRIMARY KEY (workspace_id, date, user_id, model_id) — upsert.
    Хранится бессрочно, usage_event чистятся через 90 дней (T-406).
    """

    __tablename__ = "usage_daily"

    # Переопределяем workspace_id с primary_key для composite PK
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspace.id"),
        nullable=False,
        primary_key=True,
    )

    date: Mapped[str] = mapped_column(String(10), nullable=False, primary_key=True, index=True)
    # BUG-008: sentinel UUID вместо NULL — PostgreSQL PK implicit NOT NULL.
    # FK убран: sentinel не ссылается на реального user/model (T-406 retention).
    user_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        primary_key=True,
    )
    model_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        primary_key=True,
    )
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Corpus(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Корпус документов — контейнер для RAG.

    data_class (К0–К3) — ADR-12: определяет канал вывода.
    pinned_model_id — для К2/К3 фиксирует модель, не давая пользователю выбирать.
    active_index_version_id — FK на index_version добавляется в T-205,
    здесь — nullable str без FK-constraint (таблицы index_version ещё нет).
    """

    __tablename__ = "corpus"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_corpus_workspace_name"),)
    # workspace_id индекс создаётся WorkspaceMixin (index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    data_class: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # FK добавляется в T-205 через ALTER TABLE после создания index_version
    # use_alter=True: разрывает цикл corpus↔index_version для DDL-сортировки
    # ondelete=SET NULL: удаление index_version не блокирует corpus
    active_index_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "index_version.id",
            use_alter=True,
            name="fk_corpus_active_index_version_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    pinned_model_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("model.id"),
        nullable=True,
    )

    documents: Mapped[list[Document]] = relationship(
        back_populates="corpus",
        cascade="all, delete-orphan",
    )
    index_versions: Mapped[list[IndexVersion]] = relationship(
        back_populates="corpus",
        cascade="all, delete-orphan",
        foreign_keys="IndexVersion.corpus_id",
    )


class Document(Base, IdMixin, WorkspaceMixin):
    """Документ в корпусе.

    blob_uri — sha256 hex (ключ в BlobStore).
    Удаление корпуса каскадно удаляет документы (ON DELETE CASCADE),
    но НЕ удаляет байты в blob store — ADR-7: оригинал остаётся источником
    правды, физическая очистка — отдельная явная операция (T-406).
    """

    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("corpus_id", "sha256", name="uq_document_corpus_sha256"),
        # workspace_id индекс создаётся WorkspaceMixin (index=True)
        Index("ix_document_corpus_id", "corpus_id"),
    )

    corpus_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("corpus.id", ondelete="CASCADE"),
        nullable=False,
    )
    blob_uri: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(
        String(255), nullable=False, default="application/octet-stream"
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="upload")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    corpus: Mapped[Corpus] = relationship(back_populates="documents")


class IndexVersion(Base, IdMixin, WorkspaceMixin, TimestampMixin):
    """Версия индекса корпуса (ADR-8: blue-green переиндексация).

    Статусы: building → active → retired.
    active_index_version_id в Corpus ссылается на одну из этих записей.
    """

    __tablename__ = "index_version"
    __table_args__ = (Index("ix_index_version_corpus_id", "corpus_id"),)

    corpus_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("corpus.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    chunker: Mapped[str] = mapped_column(String(50), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="building")
    stats: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="index_version",
        cascade="all, delete-orphan",
    )
    corpus: Mapped[Corpus] = relationship(
        back_populates="index_versions",
        foreign_keys=[corpus_id],
    )


class Chunk(Base, IdMixin, WorkspaceMixin):
    """Чанк документа в версии индекса (ADR-9).

    Метаданные зависят от типа: путь заголовков для документов,
    файл/язык/символ/класс/импорты для кода.
    """

    __tablename__ = "chunk"
    __table_args__ = (
        Index("ix_chunk_index_version_id", "index_version_id"),
        Index("ix_chunk_document_id", "document_id"),
    )

    index_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("index_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("document.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    meta: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)

    index_version: Mapped[IndexVersion] = relationship(back_populates="chunks")


class EvalSet(Base, IdMixin, TimestampMixin, WorkspaceMixin):
    """Набор вопросов для оценки (ADR-10, arch.md §5.1).

    Привязан к корпусу: оценка всегда для конкретного корпуса.
    Удаление корпуса каскадно удаляет наборы (ON DELETE CASCADE).
    """

    __tablename__ = "eval_set"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_eval_set_workspace_name"),
        Index("ix_eval_set_corpus_id", "corpus_id"),
    )

    corpus_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("corpus.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    items: Mapped[list[EvalItem]] = relationship(
        back_populates="eval_set",
        cascade="all, delete-orphan",
    )
    runs: Mapped[list[EvalRun]] = relationship(
        back_populates="eval_set",
        cascade="all, delete-orphan",
    )


class EvalItem(Base, IdMixin, WorkspaceMixin):
    """Вопрос в наборе оценки (arch.md §5.1).

    expected_doc_ids — список UUID документов, содержащих ответ.
    expected_answer — эталонный ответ (опционально).
    """

    __tablename__ = "eval_item"
    __table_args__ = (Index("ix_eval_item_eval_set_id", "eval_set_id"),)

    eval_set_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("eval_set.id", ondelete="CASCADE"),
        nullable=False,
    )
    question: Mapped[str] = mapped_column(String, nullable=False)
    expected_doc_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    expected_answer: Mapped[str | None] = mapped_column(String, nullable=True)

    eval_set: Mapped[EvalSet] = relationship(back_populates="items")


class EvalRun(Base, IdMixin, WorkspaceMixin):
    """Прогон оценки (arch.md §5.1, ADR-10).

    index_version_id — nullable + ON DELETE SET NULL (как T-117/T-118 для
    trace/usage_event). cleanup_retired_versions (T-215) удаляет chunks +
    vectors + index_version для retired-версий; каскадное удаление eval_run
    уничтожило бы историю прогонов — ценность ADR-10 в сравнении метрик
    между версиями после их вывода из эксплуатации (T-226).
    """

    __tablename__ = "eval_run"
    __table_args__ = (
        Index("ix_eval_run_eval_set_id", "eval_set_id"),
        Index("ix_eval_run_index_version_id", "index_version_id"),
    )

    eval_set_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("eval_set.id", ondelete="CASCADE"),
        nullable=False,
    )
    index_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("index_version.id", ondelete="SET NULL"),
        nullable=True,
    )
    pipeline: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    eval_set: Mapped[EvalSet] = relationship(back_populates="runs")
