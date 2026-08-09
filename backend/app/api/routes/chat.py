"""POST /api/chat — полный конвейер arch.md §7.1.

Порядок: аутентификация → resolve_policy → enforce → маршрутизация → стрим/complete → save.
S-13: обрыв не теряет учёт, ошибка — событием, не разрывом.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.chat import ChatRequest
from app.auth.dependencies import current_user
from app.chat.service import (
    ChatContext,
    execute_complete,
    execute_stream,
    prepare_chat,
    save_messages,
)
from app.db.models import Model, Provider, Role, User
from app.db.session import get_session
from app.policy.rate_limiter import RateLimiter
from app.policy.resolve import resolve_policy

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(current_user)])


@router.post("", response_model=None)
async def chat(
    body: ChatRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse | dict[str, object]:
    """Обработка чат-запроса. Стриминг или обычный режим.

    Полный конвейер §7.1:
    1. resolve_policy(user)
    2. enforce (класс данных, модель, контекст, rate limits)
    3. маршрутизация → выбор модели
    4. выполнение запроса
    5. сохранение сообщений
    """
    secret_key: str = request.app.state.secret_key
    workspace_id: str = request.app.state.workspace_id
    rate_limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)

    policy = await resolve_policy(session, user)

    role_result = await session.execute(select(Role).where(Role.id == user.role_id))
    role = role_result.scalar_one()

    messages_dicts: list[dict[str, str]] = [
        {"role": m.role, "content": m.content} for m in body.messages
    ]

    chat_ctx, model, provider, _fallbacks = await prepare_chat(
        session=session,
        user=user,
        role_name=role.name,
        policy=policy,
        messages=messages_dicts,
        model_alias=body.model_alias,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
        stream=body.stream,
        corpus_data_class=body.corpus_data_class,
        corpus_name=body.corpus_name,
        task_type=body.task_type,
        conversation_id=body.conversation_id,
        rate_limiter=rate_limiter,
        secret_key=secret_key,
        workspace_id=workspace_id,
    )

    if body.stream:
        session_factory: async_sessionmaker[AsyncSession] = request.app.state.db_session_factory

        return StreamingResponse(
            _stream_with_save(
                chat_ctx,
                model,
                provider,
                secret_key,
                workspace_id,
                session_factory,
            ),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    # Non-streaming mode
    result = await execute_complete(chat_ctx, model, provider, secret_key)
    conv_id = await save_messages(session, chat_ctx, model, workspace_id)
    result["conversation_id"] = conv_id
    return result


async def _stream_with_save(
    chat_ctx: ChatContext,
    model: Model,
    provider: Provider,
    secret_key: str,
    workspace_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[str]:
    """Обёртка: стримит токены, затем сохраняет сообщения в finally.

    S-13: сохранение выполняется даже при обрыве соединения.
    Использует отдельную сессию — роутерная уже закрыта после возврата StreamingResponse.
    """
    try:
        async for chunk in execute_stream(chat_ctx, model, provider, secret_key):
            yield chunk
    finally:
        async with session_factory() as save_session:
            await save_messages(save_session, chat_ctx, model, workspace_id)
