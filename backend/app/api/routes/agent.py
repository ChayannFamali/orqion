"""POST /api/agent/chat — агентный цикл «модель → инструменты → модель» (Т-502).

Решения пересмотренного дизайн-ревью:

- пункт 2 — синхронный прогон в одном запросе (без фоновых задач и
  чекпоинтов);
- пункт 6 — обязательный MVP-инструмент «поиск по корпусу», обёртка над
  существующим RAG-конвейером;
- пункт 7 — гарантии ADR-21 буквально: ``policy.corpora`` с тем же
  отказом, самый строгий класс данных, дуальный аудит вызовов
  инструментов;
- пункт 8 — каждый вызов модели проходит ``enforce`` и ``record_usage``
  идентично обычному чату;
- пункт 10 — отдельная точка входа: разговоры агента создаются в режиме
  ``agent``, обычный чат поведение не меняет.

Честная деградация (паттерн Т-444/Т-505): без дополнения
``orqion[agent]`` эндпоинт отвечает 200 с ``available=false`` и явной
причиной, а не падает.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import AgentRunConfig, run_agent_loop
from app.agent.runtime import is_agent_available
from app.api.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentStepEntry,
    PendingConfirmation,
)
from app.api.schemas.chat import ChatSourceEntry, ChatUsage
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.chat.service import ChatContext, save_messages
from app.db.models import Conversation, Corpus, Model, Provider, User
from app.db.session import get_session
from app.errors import (
    AgentRunLimitExceeded,
    BadRequest,
    DataClassViolation,
    NotFound,
    OrqionError,
)
from app.mcp.registry import resolve_tools
from app.policy.enforce import enforce_all
from app.policy.resolve import resolve_policy
from app.rag.service import resolve_corpora, strictest_data_class
from app.trace.service import create_trace, finalize_trace, span
from app.utils.tokens import count_tokens

router = APIRouter(prefix="/api/agent", tags=["agent"], dependencies=[Depends(current_user)])

UNAVAILABLE_REASON = (
    "Агентный модуль недоступен: требуется дополнительный компонент. "
    "Установите orqion[agent]: pip install orqion[agent]"
)


@dataclass
class _RequestAction:
    """Действие для ``enforce`` на уровне запроса — те же поля, что в чате."""

    model_alias: str
    model_locality: str
    input_tokens: int
    output_tokens: int
    corpus_data_class: str | None
    corpus_name: str | None
    corpus_names: list[str] | None = None


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(
    body: AgentChatRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentChatResponse:
    """Один синхронный агентный прогон по вопросу пользователя."""
    if not is_agent_available():
        return AgentChatResponse(available=False, reason=UNAVAILABLE_REASON)

    if not body.messages:
        raise BadRequest(
            "Список сообщений пуст",
            hint="Добавьте вопрос пользователя последним сообщением",
        )

    secret_key: str = request.app.state.secret_key
    workspace_id: str = request.app.state.workspace_id
    rate_limiter = getattr(request.app.state, "rate_limiter", None)

    trace_ctx = await create_trace(session, workspace_id, user_id=user.id)
    policy = await resolve_policy(session, user)

    # Корпуса — источник истины для класса данных (как в чате, Т-221/Т-439):
    # отказ при ненайденном/неготовом корпусе — тот же, что в обычном чате.
    requested_names = list(dict.fromkeys(body.corpus_names or []))
    if any(not name.strip() for name in requested_names):
        raise BadRequest(
            "Имя корпуса не может быть пустым",
            hint="Уберите пустые значения из списка корпусов",
        )
    corpora: list[Corpus] = []
    corpus_data_class: str | None = None
    if requested_names:
        async with span(trace_ctx, "resolve_corpora"):
            corpora = await resolve_corpora(session, workspace_id, requested_names)
        corpus_data_class = strictest_data_class([c.data_class for c in corpora])

    # Модель: явный выбор точки входа, только с флагом администратора.
    model_result = await session.execute(
        select(Model).where(Model.workspace_id == workspace_id, Model.alias == body.model_alias)
    )
    model = model_result.scalar_one_or_none()
    if model is None or not model.enabled:
        raise NotFound(
            constraint={"object": "model", "alias": body.model_alias},
            hint="Модель не найдена или отключена",
        )
    if not model.supports_tools:
        raise BadRequest(
            "Модель не отмечена как пригодная для агентного режима",
            constraint={"model": body.model_alias},
            hint="Администратор должен включить флаг «Модель подходит для агентного режима»",
        )
    provider = (
        await session.execute(select(Provider).where(Provider.id == model.provider_id))
    ).scalar_one_or_none()
    if provider is None or not provider.enabled:
        raise NotFound(
            constraint={"object": "provider", "model": body.model_alias},
            hint="Провайдер модели не найден или отключён",
        )

    messages_dicts = [{"role": m.role, "content": m.content} for m in body.messages]
    input_tokens = count_tokens("".join(m["content"] for m in messages_dicts))
    output_tokens = body.max_tokens or model.max_output_tokens or 1024

    # Запрос уровня политики — идентично обычному чату (пункт 8): та же
    # проверка класса данных, корпусов, модели, лимитов и бюджета.
    try:
        async with span(trace_ctx, "enforce"):
            await enforce_all(
                policy,
                _RequestAction(
                    model_alias=model.alias,
                    model_locality=model.locality,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    corpus_data_class=corpus_data_class,
                    corpus_name=requested_names[0] if requested_names else None,
                    corpus_names=requested_names or None,
                ),
                session=session,
                user_id=user.id,
                workspace_id=workspace_id,
                rate_limiter=rate_limiter,
                model_cost_in=model.cost_in,
                model_cost_out=model.cost_out,
            )
    except DataClassViolation as exc:
        # Прецедент чата: нарушение ADR-12 фиксируется в журнале аудита.
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_user_id=user.id,
            action="security.data_class_violation",
            object_type="agent",
            meta={
                "error": exc.error_code,
                "reason": exc.reason,
                "constraint": exc.constraint,
                "model_alias": body.model_alias,
                "corpus_names": requested_names or None,
            },
        )
        await session.commit()
        raise

    # Разговор агентного режима: создаётся лениво при первом прогоне,
    # как разговор чата; существующий обязан быть в режиме "agent".
    if body.conversation_id is not None:
        conv_result = await session.execute(
            select(Conversation).where(
                Conversation.id == body.conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.user_id == user.id,
            )
        )
        conv = conv_result.scalar_one_or_none()
        if conv is None:
            raise NotFound(
                constraint={"object": "conversation", "id": body.conversation_id},
                hint="Диалог не найден",
            )
        if conv.mode != "agent":
            raise BadRequest(
                "Диалог не в агентном режиме",
                constraint={"mode": conv.mode},
                hint="Создайте агентный диалог отдельной точкой входа",
            )
        conversation = conv
    else:
        conversation = Conversation(
            workspace_id=workspace_id,
            user_id=user.id,
            title=messages_dicts[-1]["content"][:80],
            archived=False,
            mode="agent",
        )
        session.add(conversation)
        await session.flush()

    # Единый реестр инструментов прогона (Т-503, решение 4): встроенные
    # инструменты и инструменты внешних серверов собираются в один список
    # ДО запуска цикла. Сборка идёт после ``enforce`` — отклонённый
    # политикой запрос не обращается к внешним серверам. Класс данных
    # К2/К3 отклоняет вынос ещё до обнаружения; недоступный сервер
    # скрывает свои инструменты и пишет факт в журнал аудита в самом
    # пути сборки (решение 6).
    async with span(trace_ctx, "resolve_tools"):
        tools_registry = await resolve_tools(
            session,
            settings=request.app.state.settings,
            secret_key=secret_key,
            workspace_id=workspace_id,
            user_id=user.id,
            trace_ctx=trace_ctx,
            conversation_id=conversation.id,
            corpus_data_class=corpus_data_class,
        )

    cfg = AgentRunConfig(
        session=session,
        settings=request.app.state.settings,
        secret_key=secret_key,
        workspace_id=workspace_id,
        user=user,
        policy=policy,
        model=model,
        provider=provider,
        vector_store=request.app.state.vector_store,
        embedding_backend=request.app.state.embedding_backend,
        corpora=corpora,
        corpus_names=requested_names,
        corpus_data_class=corpus_data_class,
        conversation_id=conversation.id,
        rate_limiter=rate_limiter,
        trace_ctx=trace_ctx,
        max_steps=request.app.state.settings.agent_max_steps,
        max_tokens_per_run=request.app.state.settings.agent_max_tokens_per_run,
        tools_registry=tools_registry,
    )

    try:
        async with span(trace_ctx, "agent.run"):
            result = await run_agent_loop(cfg, messages_dicts)
    except (AgentRunLimitExceeded, OrqionError) as exc:
        # Сообщение пользователя сохраняем даже при остановке прогона —
        # диалог не теряет вопрос (ошибка — событием, не потерей данных).
        await _save_turn_on_error(session, cfg, messages_dicts, workspace_id)
        await finalize_trace(
            session,
            trace_ctx,
            conversation_id=conversation.id,
            message_id=None,
            error=True,
        )
        if isinstance(exc, AgentRunLimitExceeded):
            # Совокупное потребление уже записано побиллингово (каждый
            # вызов модели — отдельный usage_event); в ответе поле не
            # заполняем, чтобы не выдавать оценку за факт.
            return AgentChatResponse(
                type="error",
                code=exc.error_code,
                constraint=exc.constraint,
                hint=exc.hint,
                conversation_id=conversation.id,
                model=model.alias,
                usage=None,
                trace_id=trace_ctx.trace_id,
            )
        raise

    # Источники: дедупликация по чанку с сохранением порядка вызовов.
    seen: set[str] = set()
    sources: list[ChatSourceEntry] = []
    for s in result.sources:
        if s.chunk_id in seen:
            continue
        seen.add(s.chunk_id)
        sources.append(
            ChatSourceEntry(
                chunk_id=s.chunk_id,
                document_id=s.document_id,
                structural_path=s.structural_path,
                score=s.score,
                original_rank=s.original_rank,
                corpus_id=s.corpus_id,
                corpus_name=s.corpus_name,
            )
        )

    # Сохранение сообщений — общий механизм чата (включая FTS и заголовок).
    chat_ctx = _build_chat_context(cfg, messages_dicts, result.content)
    conv_id, msg_id = await save_messages(
        session, chat_ctx, model, workspace_id, sources=result.sources
    )

    await finalize_trace(
        session,
        trace_ctx,
        conversation_id=conv_id,
        message_id=msg_id,
        error=False,
    )

    pending = (
        PendingConfirmation(
            call_id=str(result.pending_confirmation.get("call_id", "")),
            tool=str(result.pending_confirmation.get("tool", "")),
            args=dict(result.pending_confirmation.get("args", {})),
        )
        if result.pending_confirmation
        else None
    )

    return AgentChatResponse(
        available=True,
        type="complete",
        content=result.content,
        conversation_id=conv_id,
        model=model.alias,
        usage=ChatUsage(tokens_in=result.tokens_in, tokens_out=result.tokens_out),
        steps=[
            AgentStepEntry(
                index=s.index,
                kind=s.kind,
                name=s.name,
                summary=s.summary,
                decision=s.decision,
            )
            for s in result.steps
        ],
        sources=sources,
        trace_id=trace_ctx.trace_id,
        pending_confirmation=pending,
    )


def _build_chat_context(
    cfg: AgentRunConfig,
    messages_dicts: list[dict[str, str]],
    answer: str,
) -> ChatContext:
    """Контекст для общего механизма сохранения сообщений чата."""
    chat_ctx = ChatContext(
        user=cfg.user,
        policy=cfg.policy,
        messages=messages_dicts,
        model_alias=cfg.model.alias,
        max_tokens=None,
        temperature=0.7,
        stream=False,
        corpus_data_class=cfg.corpus_data_class,
        corpus_names=cfg.corpus_names or None,
        task_type=None,
        conversation_id=cfg.conversation_id,
        rate_limiter=cfg.rate_limiter,
        tokens_in=count_tokens("".join(m["content"] for m in messages_dicts)),
        model_id=cfg.model.id,
    )
    if answer:
        chat_ctx.accumulated_content = [answer]
    return chat_ctx


async def _save_turn_on_error(
    session: AsyncSession,
    cfg: AgentRunConfig,
    messages_dicts: list[dict[str, str]],
    workspace_id: str,
) -> None:
    """Сообщение пользователя сохраняется и при остановке прогона."""
    chat_ctx = _build_chat_context(cfg, messages_dicts, "")
    chat_ctx.error_code = "agent_run_failed"
    try:
        await save_messages(session, chat_ctx, cfg.model, workspace_id)
    except Exception:  # noqa: BLE001  сохранение не маскирует исходную ошибку
        await session.rollback()
