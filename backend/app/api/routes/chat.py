"""POST /api/chat — полный конвейер arch.md §7.1.

Порядок: аутентификация → resolve_policy → enforce → маршрутизация → стрим/complete → save + usage + trace.
S-13: обрыв не теряет учёт, ошибка — событием, не разрывом.
ADR-14: trace + span для каждого запроса.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.chat import ChatRequest, ChatResponse, ChatSourceEntry, ChatUsage
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.chat.service import (
    ChatContext,
    execute_complete,
    execute_stream,
    prepare_chat,
    save_messages,
)
from app.config import Settings
from app.db.models import Corpus, Model, Provider, Role, User
from app.db.session import get_session
from app.errors import (
    BadRequest,
    DatabaseTemporarilyUnavailable,
    DataClassViolation,
    NoRouteAvailable,
)
from app.metrics.registry import record_chat_request, record_rag_query
from app.policy.rate_limiter import RateLimiter
from app.policy.resolve import resolve_policy
from app.rag.pipeline import RagContext, RagState, run_pipeline
from app.rag.service import resolve_corpora
from app.trace.service import TraceContext, create_trace, finalize_trace, span
from app.usage.service import UsageRecord, calculate_cost, record_usage

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(current_user)])

# T-439 (решение А1): строгость классов данных. Любой К2/К3 среди выбранных
# корпусов переводит весь запрос на локальные модели.
_DATA_CLASS_STRICTNESS: dict[str, int] = {"К0": 0, "К1": 1, "К2": 2, "К3": 3}


def _strictest_data_class(classes: list[str | None]) -> str | None:
    """Самый строгий data_class побеждает; None трактуется как К0."""
    strictest: str | None = None
    for cls in classes:
        if cls is None:
            continue
        if strictest is None or _DATA_CLASS_STRICTNESS.get(cls, 0) > _DATA_CLASS_STRICTNESS.get(
            strictest, 0
        ):
            strictest = cls
    return strictest


def _is_adr12_violation(exc: DataClassViolation | NoRouteAvailable) -> bool:
    """True если ошибка связана с ограничением ADR-12 (data_class в constraint)."""
    constraint = exc.constraint or {}
    return "data_class" in constraint


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


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse | ChatResponse:
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

    # T-221/T-439: резолв корпусов — корпуса являются источником истины для
    # data_class и pinned_model_id (ADR-12). corpus_name (одиночный, обратная
    # совместимость) и corpus_names (мульти-режим) взаимно исключают друг друга.
    corpus_data_class = body.corpus_data_class
    model_alias = body.model_alias
    corpora: list[Corpus] = []

    if body.corpus_name is not None and body.corpus_names is not None:
        raise BadRequest(
            "Укажите corpus_name или corpus_names, но не оба поля сразу",
            hint="Поля взаимно исключают друг друга",
        )
    requested_names: list[str] = []
    if body.corpus_names is not None:
        # dict.fromkeys — дедупликация с сохранением порядка (детерминизм Д1)
        requested_names = list(dict.fromkeys(body.corpus_names))
    elif body.corpus_name is not None:
        requested_names = [body.corpus_name]

    if any(not name.strip() for name in requested_names):
        raise BadRequest(
            "Имя корпуса не может быть пустым",
            hint="Уберите пустые значения из списка корпусов",
        )

    if requested_names:
        # Решения дизайн-ревью T-439: fail-closed (Е1) — первый ненайденный
        # или неготовый корпус роняет весь запрос (см. resolve_corpora).
        async with span(trace_ctx, "resolve_corpora"):
            corpora = await resolve_corpora(session, workspace_id, requested_names)
        # Решение А1: строжайший data_class побеждает — любой К2/К3 среди
        # выбранных корпусов делает весь запрос доступным только локальным
        # моделям (корпус переопределяет data_class из БД, как в T-221).
        corpus_data_class = _strictest_data_class([c.data_class for c in corpora])
        # Решение Д1: конфликт пинов → явная ошибка. Один общий пин
        # применяется как в одиночном режиме: переопределяет выбор
        # пользователя. Пин хранится как id модели, а маршрутизация
        # сравнивает алиасы (BUG-013) — резолвим алиас до входа в select_model.
        pins = {c.pinned_model_id for c in corpora if c.pinned_model_id is not None}
        if len(pins) > 1:
            raise BadRequest(
                "Конфликт закрепления: выбранные корпуса закреплены за разными моделями",
                constraint={"reason": "pin_conflict", "corpora": requested_names},
                hint="Выберите корпуса с общим пином или снимите закрепление",
            )
        if len(pins) == 1:
            pinned_model_id = next(iter(pins))
            pinned_result = await session.execute(select(Model).where(Model.id == pinned_model_id))
            pinned_model = pinned_result.scalar_one_or_none()
            if pinned_model is None:
                # FK corpus.pinned_model_id → model.id при PRAGMA foreign_keys=ON
                # делает ветку недостижимой при целостных данных.
                raise NoRouteAvailable(
                    constraint={
                        "reason": "pinned_model_not_found",
                        "corpus_names": requested_names,
                    },
                    hint="Модель, закреплённая за корпусом, не найдена",
                )
            model_alias = pinned_model.alias

    async with span(trace_ctx, "prepare"):
        try:
            chat_ctx, model, provider, fallbacks = await prepare_chat(
                session=session,
                user=user,
                role_name=role.name,
                policy=policy,
                messages=messages_dicts,
                model_alias=model_alias,
                max_tokens=body.max_tokens,
                temperature=body.temperature,
                stream=body.stream,
                corpus_data_class=corpus_data_class,
                corpus_names=requested_names or None,
                task_type=body.task_type,
                conversation_id=body.conversation_id,
                rate_limiter=rate_limiter,
                secret_key=secret_key,
                workspace_id=workspace_id,
                trace_ctx=trace_ctx,
            )
            chat_ctx.session = session
            chat_ctx.settings = Settings()
        except (DataClassViolation, NoRouteAvailable) as exc:
            if _is_adr12_violation(exc):
                await write_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_user_id=user.id,
                    action="security.data_class_violation",
                    object_type="chat",
                    meta={
                        "error": exc.error_code,
                        "reason": exc.reason,
                        "constraint": exc.constraint,
                        "model_alias": model_alias,
                        "corpus_names": requested_names or None,
                    },
                )
                await session.commit()
            raise
        chat_ctx.trace_id = trace_ctx.trace_id

    # Флашим trace + prepare данные до возврата StreamingResponse,
    # иначе SQLite блокируется при записи в _stream_with_save.
    # OperationalError (database is locked) — деградирует: trace потерян,
    # но chat продолжается (S-14: трассировка не блокирует чат).
    # Использует begin_nested (SAVEPOINT) чтобы при rollback не экспайрить
    # ORM-объекты в сессии (user, model, provider).
    try:
        async with session.begin_nested():
            await session.flush()
    except OperationalError:
        import logging

        logging.getLogger("orqion.chat").warning(
            "Failed to flush trace+prepare: degrading (database is locked)"
        )

    # T-221/T-439: RAG-конвейер, если задан хотя бы один корпус
    if corpora:
        vector_store = request.app.state.vector_store
        embedding_backend = request.app.state.embedding_backend

        rag_state = RagState(
            query=messages_dicts[-1]["content"],
            trace_id=trace_ctx.trace_id,
        )
        rag_ctx = RagContext(
            session=session,
            settings=request.app.state.settings,
            vector_store=vector_store,
            embedding_backend=embedding_backend,
            secret_key=secret_key,
            workspace_id=workspace_id,
            index_version_id=corpora[0].active_index_version_id or "",
            index_version_ids=[c.active_index_version_id or "" for c in corpora],
            corpus_attribution={c.active_index_version_id or "": (c.id, c.name) for c in corpora},
            model=model,
            provider=provider,
            trace_ctx=trace_ctx,
            messages=messages_dicts,
        )

        async with span(trace_ctx, "rag_pipeline"):
            rag_state = await run_pipeline(rag_state, rag_ctx)

        # usage_event — всегда, даже при degraded/упавшем generate
        # degraded=True от ранних шагов (rewrite/rerank) — НЕ ошибка биллинга:
        # step_generate всё равно вызвал провайдера и потратил токены.
        # status="error" только когда ответ не получен (usage is None).
        rag_usage = rag_state.usage or {}
        tokens_in = rag_usage.get("prompt_tokens", 0)
        tokens_out = rag_usage.get("completion_tokens", 0)
        cost = calculate_cost(tokens_in, tokens_out, model.cost_in, model.cost_out)
        latency_ms = (
            int(1000 * (time.monotonic() - chat_ctx.started_at)) if chat_ctx.started_at else 0
        )
        generate_failed = rag_state.usage is None
        status = "error" if generate_failed else "ok"

        # RAG answer не накапливается в chat_ctx.accumulated_content (pipeline
        # пишет в rag_state.answer), поэтому заполняем вручную для save_messages.
        if rag_state.answer:
            chat_ctx.accumulated_content = [rag_state.answer]

        conv_id, msg_id = await save_messages(
            session, chat_ctx, model, workspace_id, sources=rag_state.sources
        )

        usage_record = UsageRecord(
            user_id=user.id,
            model_id=model.id,
            conversation_id=conv_id,
            message_id=msg_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            latency_ms=latency_ms,
            status=status,
            error_code="rag_generate_failed" if generate_failed else None,
        )
        await record_usage(session, workspace_id, usage_record)

        await finalize_trace(
            session,
            trace_ctx,
            conversation_id=conv_id,
            message_id=msg_id,
            error=rag_state.degraded,
        )

        # T-433: генерация заголовка диалога fire-and-forget — только для
        # нового диалога (chat_ctx.conversation_id is None → save_messages
        # создал новый). Не блокирует ответ, своя сессия, своя задача.
        if chat_ctx.conversation_id is None and rag_state.answer:
            from app.rag.title_generation import generate_title_background

            first_user_msg = next(
                (m for m in chat_ctx.messages if m["role"] == "user"),
                None,
            )
            if first_user_msg:
                generate_title_background(
                    session_factory=request.app.state.db_session_factory,
                    settings=request.app.state.settings,
                    secret_key=secret_key,
                    workspace_id=workspace_id,
                    conversation_id=conv_id,
                    first_user_message=first_user_msg["content"],
                    first_assistant_message=rag_state.answer,
                    user_id=user.id,
                    background_tasks=request.app.state.background_tasks,
                )

        result = ChatResponse(
            type="complete",
            content=rag_state.answer or "",
            usage=ChatUsage(tokens_in=tokens_in, tokens_out=tokens_out),
            model=model.alias,
            conversation_id=conv_id,
            rag_degraded=rag_state.degraded,
            rag_errors=rag_state.errors if rag_state.degraded else [],
            sources=[
                ChatSourceEntry(
                    chunk_id=s.chunk_id,
                    document_id=s.document_id,
                    structural_path=s.structural_path,
                    score=s.score,
                    original_rank=s.original_rank,
                    corpus_id=s.corpus_id,
                    corpus_name=s.corpus_name,
                )
                for s in rag_state.sources
            ],
        )
        record_rag_query(status=status)
        rag_latency_s = (time.monotonic() - chat_ctx.started_at) if chat_ctx.started_at else 0.0
        record_chat_request(
            status=status,
            error_code="rag_generate_failed" if generate_failed else "",
            duration_seconds=rag_latency_s,
        )
        return result

    if body.stream:
        session_factory: async_sessionmaker[AsyncSession] = request.app.state.db_session_factory

        return StreamingResponse(
            _stream_with_save(
                request,
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
        raw_result = await execute_complete(chat_ctx, model, provider, secret_key, fallbacks)

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

    raw_result["conversation_id"] = conv_id

    latency_s = (time.monotonic() - chat_ctx.started_at) if chat_ctx.started_at else 0.0
    status = "error" if chat_ctx.error_code else "ok"
    record_chat_request(
        status=status, error_code=chat_ctx.error_code or "", duration_seconds=latency_s
    )

    return ChatResponse.model_validate(raw_result)


async def _stream_with_save(
    request: Request,
    chat_ctx: ChatContext,
    model: Model,
    provider: Provider,
    fallbacks: list[tuple[Model, Provider]],
    secret_key: str,
    workspace_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    trace_ctx: TraceContext,
) -> AsyncGenerator[str, None]:
    """Обёртка: стримит токены, затем сохраняет сообщения + usage_event + trace в finally.

    S-13: сохранение, учёт и трассировка выполняются даже при обрыве соединения.
    Использует отдельную сессию — роутерная уже закрыта после возврата StreamingResponse.

    T-116b: передаёт fallbacks в execute_stream. После выполнения определяет
    фактическую модель (основная или fallback) по chat_ctx.model_id.

    T-305: проверяет request.is_disconnected() на каждый чанк — при обрыве
    клиентского соединения upstream-генерация останавливается, а не
    продолжается вхолостую. Явный gen.aclose() в finally гарантирует
    закрытие upstream HTTP-соединения к провайдеру, не полагается на GC.
    """
    gen = execute_stream(chat_ctx, model, provider, secret_key, fallbacks)
    try:
        async with span(trace_ctx, "stream"):
            async for chunk in gen:
                if await request.is_disconnected():
                    break
                yield chunk
    finally:
        await gen.aclose()
        # Фактическая модель — могла смениться на fallback
        actual_model = model
        if chat_ctx.model_id is not None and chat_ctx.model_id != model.id:
            actual_model = _find_model(fallbacks, chat_ctx.model_id) or model

        conv_id: str | None = None
        msg_id: str | None = None
        save_error: DatabaseTemporarilyUnavailable | None = None

        try:
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
        except DatabaseTemporarilyUnavailable as e:
            save_error = e
            yield f"data: {json.dumps({'type': 'error', 'code': e.error_code, 'message': e.reason, 'reason': e.reason, 'hint': e.hint})}\n\n"
            yield "data: [DONE]\n\n"

        latency_s = (time.monotonic() - chat_ctx.started_at) if chat_ctx.started_at else 0.0
        stream_status = "error" if (chat_ctx.error_code or save_error) else "ok"
        record_chat_request(
            status=stream_status,
            error_code=chat_ctx.error_code or (save_error.error_code if save_error else ""),
            duration_seconds=latency_s,
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
