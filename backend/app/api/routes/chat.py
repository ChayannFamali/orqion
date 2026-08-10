"""POST /api/chat — полный конвейер arch.md §7.1.

Порядок: аутентификация → resolve_policy → enforce → маршрутизация → стрим/complete → save + usage + trace.
S-13: обрыв не теряет учёт, ошибка — событием, не разрывом.
ADR-14: trace + span для каждого запроса.
"""

from __future__ import annotations

import time
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
from app.trace.service import TraceContext, create_trace, finalize_trace, span
from app.usage.service import UsageRecord, calculate_cost, record_usage

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(current_user)])


def _build_usage_record(
    chat_ctx: ChatContext,
    model: Model,
    conversation_id: str | None,
    message_id: str | None = None,
) -> UsageRecord:
    """Формирует запись usage_event из контекста чата."""
    latency_ms = int(
        (chat_ctx.started_at and (1000 * (time.monotonic() - chat_ctx.started_at))) or 0
    )
    status = "error" if chat_ctx.error_code else "ok"
    cost = calculate_cost(
        chat_ctx.tokens_in,
        chat_ctx.tokens_out,
        model.cost_in,
        model.cost_out,
    )
    return UsageRecord(
        user_id=chat_ctx.user.id,
        model_id=model.id,
        conversation_id=conversation_id,
        message_id=message_id,
        tokens_in=chat_ctx.tokens_in,
        tokens_out=chat_ctx.tokens_out,
        cost=cost,
        latency_ms=latency_ms,
        status=status,
        error_code=chat_ctx.error_code,
    )


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
    5. сохранение сообщений + usage_event + trace
    """
    secret_key: str = request.app.state.secret_key
    workspace_id: str = request.app.state.workspace_id
    rate_limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)

    # Создаём trace
    trace_ctx = await create_trace(session, workspace_id, user_id=user.id)

    policy = await resolve_policy(session, user)

    role_result = await session.execute(select(Role).where(Role.id == user.role_id))
    role = role_result.scalar_one()

    messages_dicts: list[dict[str, str]] = [
        {"role": m.role, "content": m.content} for m in body.messages
    ]

    async with span(trace_ctx, "prepare"):
        chat_ctx, model, provider, fallbacks = await prepare_chat(
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
        chat_ctx.trace_id = trace_ctx.trace_id

    # Коммитим trace + prepare данные до возврата StreamingResponse,
    # иначе SQLite блокируется при записи в _stream_with_save
    await session.commit()

    if body.stream:
        session_factory: async_sessionmaker[AsyncSession] = request.app.state.db_session_factory

        return StreamingResponse(
            _stream_with_save(
                chat_ctx,
                model,
                provider,
                fallbacks,
                secret_key,
                workspace_id,
                session_factory,
                trace_ctx,
            ),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    # Non-streaming mode
    async with span(trace_ctx, "execute"):
        result = await execute_complete(chat_ctx, model, provider, secret_key, fallbacks)

    # Фактическая модель — могла смениться на fallback
    actual_model = model
    if chat_ctx.model_id is not None and chat_ctx.model_id != model.id:
        actual_model = _find_model(fallbacks, chat_ctx.model_id) or model

    conv_id, msg_id = await save_messages(session, chat_ctx, actual_model, workspace_id)

    # Запись usage_event (non-stream — та же сессия)
    usage_record = _build_usage_record(chat_ctx, actual_model, conv_id, msg_id)
    await record_usage(session, workspace_id, usage_record)

    # Финализация trace
    await finalize_trace(
        session,
        trace_ctx,
        conversation_id=conv_id,
        message_id=msg_id,
        error=chat_ctx.error_code is not None,
    )

    result["conversation_id"] = conv_id
    return result


async def _stream_with_save(
    chat_ctx: ChatContext,
    model: Model,
    provider: Provider,
    fallbacks: list[tuple[Model, Provider]],
    secret_key: str,
    workspace_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    trace_ctx: TraceContext,
) -> AsyncIterator[str]:
    """Обёртка: стримит токены, затем сохраняет сообщения + usage_event + trace в finally.

    S-13: сохранение, учёт и трассировка выполняются даже при обрыве соединения.
    Использует отдельную сессию — роутерная уже закрыта после возврата StreamingResponse.

    T-116b: передаёт fallbacks в execute_stream. После выполнения определяет
    фактическую модель (основная или fallback) по chat_ctx.model_id.
    """
    try:
        async with span(trace_ctx, "stream"):
            async for chunk in execute_stream(chat_ctx, model, provider, secret_key, fallbacks):
                yield chunk
    finally:
        # Фактическая модель — могла смениться на fallback
        actual_model = model
        if chat_ctx.model_id is not None and chat_ctx.model_id != model.id:
            actual_model = _find_model(fallbacks, chat_ctx.model_id) or model

        async with session_factory() as save_session:
            conv_id, msg_id = await save_messages(
                save_session, chat_ctx, actual_model, workspace_id
            )

            usage_record = _build_usage_record(chat_ctx, actual_model, conv_id, msg_id)
            await record_usage(save_session, workspace_id, usage_record)

            await finalize_trace(
                save_session,
                trace_ctx,
                conversation_id=conv_id,
                message_id=msg_id,
                error=chat_ctx.error_code is not None,
            )


def _find_model(
    fallbacks: list[tuple[Model, Provider]],
    model_id: str,
) -> Model | None:
    """Находит модель по id в списке fallback-моделей."""
    for m, _ in fallbacks:
        if m.id == model_id:
            return m
    return None
