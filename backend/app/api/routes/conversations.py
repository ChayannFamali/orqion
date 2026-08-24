"""CRUD для диалогов: список, создание, чтение, переименование, архивация.

Доступ только к своим диалогам (arch.md §5.1).
Заголовок формируется по первому сообщению.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.conversation import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
    MessageResponse,
    MessageSearchResult,
)
from app.auth.dependencies import current_user
from app.db.base import _utcnow
from app.db.models import Conversation, Message, User
from app.db.session import get_session
from app.errors import FeatureNotSupported, NotFound

router = APIRouter(
    prefix="/api/conversations", tags=["conversations"], dependencies=[Depends(current_user)]
)


def _conversation_to_response(
    conv: Conversation,
    message_count: int,
) -> ConversationResponse:
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        archived=conv.archived,
        created_at=conv.created_at,
        message_count=message_count,
        context_reset_at=conv.context_reset_at,
    )


def _message_to_response(msg: Message) -> MessageResponse:
    return MessageResponse(
        id=msg.id,
        role=msg.role,
        content=msg.content,
        model_id=msg.model_id,
        tokens_in=msg.tokens_in,
        tokens_out=msg.tokens_out,
        created_at=msg.created_at,
        meta=msg.meta,
    )


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    archived: bool | None = Query(None, description="Фильтр по архиву"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ConversationListResponse:
    """Список диалогов пользователя. Только свои."""
    workspace_id = request.app.state.workspace_id
    count_subquery = (
        select(func.count(Message.id))
        .where(Message.conversation_id == Conversation.id)
        .scalar_subquery()
    )
    base_query = select(Conversation, count_subquery.label("msg_count")).where(
        Conversation.workspace_id == workspace_id,
        Conversation.user_id == user.id,
    )
    if archived is not None:
        base_query = base_query.where(Conversation.archived.is_(archived))
    base_query = base_query.order_by(Conversation.created_at.desc()).limit(limit).offset(offset)

    result = await session.execute(base_query)
    rows = result.all()

    count_query = (
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user.id,
        )
    )
    if archived is not None:
        count_query = count_query.where(Conversation.archived.is_(archived))
    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    return ConversationListResponse(
        conversations=[_conversation_to_response(conv, msg_count) for conv, msg_count in rows],
        total=total,
    )


@router.post("", response_model=ConversationDetailResponse, status_code=201)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationDetailResponse:
    """Создание диалога. Заголовок — пустой до первого сообщения."""
    workspace_id = request.app.state.workspace_id
    conv = Conversation(
        workspace_id=workspace_id,
        user_id=user.id,
        title=body.title or "",
        archived=False,
    )
    session.add(conv)
    await session.flush()
    return ConversationDetailResponse(
        id=conv.id,
        title=conv.title,
        archived=conv.archived,
        created_at=conv.created_at,
        message_count=0,
        context_reset_at=conv.context_reset_at,
        messages=[],
    )


@router.get("/search", response_model=list[MessageSearchResult])
async def search_conversations(
    request: Request,
    q: str = Query(..., min_length=1, description="Поисковый запрос"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MessageSearchResult]:
    """Полнотекстовый поиск по своим диалогам (T-436).

    FTS5 + JOIN conversation WHERE user_id (до MATCH, не пост-фильтрация — §8.2).
    Экранирование — app.utils.fts5.escape_fts5_query (T-212/BUG-003).
    ВАЖНО: этот маршрут обязан стоять ДО /{conversation_id}, иначе
    "search" ловится как conversation_id (FastAPI матчит первый подходящий).

    BUG-017: на не-SQLite диалектах поиск недоступен — честный 501
    вместо молчаливого пустого результата (§7.3: отказ всегда содержит
    причину). Нативный PostgreSQL-поиск (tsvector) — отдельная задача.
    """
    from app.search.message_search import search_messages
    from app.utils.fts5 import fts5_available

    if not fts5_available(session):
        raise FeatureNotSupported(
            constraint={"object": "message_search", "requirement": "sqlite fts5"},
            hint="Полнотекстовый поиск по диалогам доступен только для SQLite",
        )

    workspace_id = request.app.state.workspace_id
    hits = await search_messages(session, q, user.id, workspace_id, limit=limit, offset=offset)
    return [
        MessageSearchResult(
            message_id=h.message_id,
            conversation_id=h.conversation_id,
            role=h.role,
            content=h.content,
            score=h.score,
        )
        for h in hits
    ]


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationDetailResponse:
    """Чтение диалога с сообщениями. Только свои."""
    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user.id,
        )
        .options(selectinload(Conversation.messages))
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise NotFound(
            constraint={"object": "conversation", "id": conversation_id},
            hint="Диалог не найден",
        )
    return ConversationDetailResponse(
        id=conv.id,
        title=conv.title,
        archived=conv.archived,
        created_at=conv.created_at,
        message_count=len(conv.messages),
        context_reset_at=conv.context_reset_at,
        messages=[_message_to_response(m) for m in conv.messages],
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """Переименование и архивация. Только свои."""
    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise NotFound(
            constraint={"object": "conversation", "id": conversation_id},
            hint="Диалог не найден",
        )

    if body.title is not None:
        conv.title = body.title
    if body.archived is not None:
        conv.archived = body.archived

    await session.flush()
    return _conversation_to_response(conv, 0)


@router.post("/{conversation_id}/reset-context", response_model=ConversationResponse)
async def reset_context(
    conversation_id: str,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """Мягкий сброс контекста диалога (T-442). Только свои.

    Устанавливает маркер context_reset_at = текущий момент. Сообщения не
    удаляются: видимая лента сохраняется, но в историю для модели на будущих
    ходах попадают только сообщения после маркера (сборка payload фильтрует
    по маркеру; см. планинг T-442). Повторный сброс сдвигает маркер вперёд.
    Бюджет и RAG-параметры запроса сброс не затрагивает.
    """
    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise NotFound(
            constraint={"object": "conversation", "id": conversation_id},
            hint="Диалог не найден",
        )

    conv.context_reset_at = _utcnow()
    await session.flush()
    await session.refresh(conv)
    # message_count=0 — прецедент PATCH: клиент рефетчит детали сам
    # (иначе потребовалась бы загрузка сообщений в ответе).
    return _conversation_to_response(conv, 0)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Удаление диалога. Только свои."""
    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise NotFound(
            constraint={"object": "conversation", "id": conversation_id},
            hint="Диалог не найден",
        )
    await session.delete(conv)

    # T-436: dual-write — удаляем FTS5-индекс сообщений диалога.
    # session.delete(conv) → ORM cascade delete-orphan удаляет Message,
    # но не FTS5 (не ORM-модель). Явный DELETE.
    # BUG-017: fts_messages существует только в SQLite — на других
    # диалектах таблицы нет, удаление пропускается.
    from app.utils.fts5 import fts5_available

    if fts5_available(session):
        from sqlalchemy import text as sa_text

        await session.execute(
            sa_text("DELETE FROM fts_messages WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        )
    await session.commit()
