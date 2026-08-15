"""Оркестрация чата: enforce → route → stream/complete → save.

arch.md §7.1 — порядок проверок:
1. resolve_policy(user)
2. класс данных корпуса → жёсткий фильтр (ADR-12, в enforce + router)
3. видимость модели (enforce)
4. лимит контекста (enforce)
5. лимит rpm/tpm (enforce)
6. маршрутизация → выбор модели + fallback
7. выполнение + сохранение сообщения

S-13: обрыв соединения не теряет учёт; ошибка — событием, не разрывом.
Сообщение сохраняется полностью — content накапливается из токенов.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import tiktoken
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Conversation, Message, Model, Provider, User
from app.detectors.service import run_detectors
from app.errors import DatabaseTemporarilyUnavailable, NotFound, OrqionError
from app.policy.enforce import enforce_all
from app.policy.models import WILDCARD, Policy
from app.policy.rate_limiter import RateLimiter
from app.providers.client import ProviderClient
from app.providers.errors import normalize_error
from app.rag.sources import SourceEntry
from app.router.models import RouteContext
from app.router.service import load_candidate_models, load_rules, select_model
from app.trace.service import TraceContext, span


class _NullSpan:
    """No-op async context manager когда trace_ctx не передан."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


_ENCODER: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    """Возвращает BPE-энкодер. cl100k_base —通用, работает для большинства моделей."""
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def _count_tokens(text: str) -> int:
    """Точный подсчёт токенов через tiktoken. T-107: корректен для кириллицы и кода."""
    if not text:
        return 1
    return len(_get_encoder().encode(text))


def _filter_by_policy_models(
    candidates: list[Model],
    policy: Policy,
) -> list[Model]:
    """Фильтрует кандидатов по policy.models — видимость модели для роли.

    policy.models=["*"] — все модели.
    Иначе — fnmatch по алиасам (например ["local/*"]).
    """
    if not policy.models or WILDCARD in policy.models:
        return candidates
    return [m for m in candidates if any(fnmatch.fnmatch(m.alias, p) for p in policy.models)]


@dataclass
class ChatContext:
    """Контекст выполнения чат-запроса."""

    user: User
    policy: Policy
    messages: list[dict[str, str]]
    model_alias: str | None
    max_tokens: int | None
    temperature: float
    stream: bool
    corpus_data_class: str | None
    corpus_name: str | None
    task_type: str | None
    conversation_id: str | None
    rate_limiter: RateLimiter | None = None
    accumulated_content: list[str] = field(default_factory=list)
    model_id: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error_code: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    trace_id: str | None = None
    session: AsyncSession | None = None
    settings: Settings | None = None


@dataclass
class _ChatAction:
    """Действие для enforce(). arch.md §7.1."""

    model_alias: str
    model_locality: str
    input_tokens: int
    output_tokens: int
    corpus_data_class: str | None
    corpus_name: str | None


async def prepare_chat(
    session: AsyncSession,
    user: User,
    role_name: str,
    policy: Policy,
    messages: list[dict[str, str]],
    model_alias: str | None,
    max_tokens: int | None,
    temperature: float,
    stream: bool,
    corpus_data_class: str | None,
    corpus_name: str | None,
    task_type: str | None,
    conversation_id: str | None,
    rate_limiter: RateLimiter | None,
    secret_key: str,
    workspace_id: str,
    trace_ctx: TraceContext | None = None,
) -> tuple[ChatContext, Model, Provider, list[tuple[Model, Provider]]]:
    """Подготовка чат-запроса: enforce, маршрутизация, загрузка модели.

    Возвращает (context, model, provider, fallbacks) где fallbacks —
    список (Model, Provider) для переключения при ошибке основной модели.
    Возбуждает доменные исключения при отказе.
    """
    # Если conversation_id задан — проверяем существование и владельца
    if conversation_id is not None:
        conv_result = await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.user_id == user.id,
            )
        )
        if conv_result.scalar_one_or_none() is None:
            raise NotFound(
                constraint={"object": "conversation", "id": conversation_id},
                hint="Диалог не найден",
            )

    # Подсчёт входных токенов через tiktoken (T-107: точно для кириллицы и кода)
    input_text = "".join(m["content"] for m in messages)
    input_tokens = _count_tokens(input_text)

    # Выходные токены — из max_tokens или оценка
    output_tokens = max_tokens if max_tokens is not None else 1024

    # Загружаем кандидатов: включённые модели с включёнными провайдерами
    candidates = await load_candidate_models(session, workspace_id)

    # Фильтр по policy.models — видимость модели для роли (до маршрутизации)
    candidates = _filter_by_policy_models(candidates, policy)

    # Если model_alias задан — используем его как контекст для маршрутизации
    ctx = RouteContext(
        candidate_models=candidates,
        user_role_name=role_name,
        model_alias=model_alias,
        corpus_data_class=corpus_data_class,
        task_type=task_type,
    )

    # Маршрутизация
    rules = await load_rules(session, workspace_id)
    routing_payload: dict[str, object] = {}
    async with span(trace_ctx, "routing", payload=routing_payload) if trace_ctx else _NullSpan():
        decision = select_model(rules, ctx)
        routing_payload["rule_index"] = decision.rule_index
        routing_payload["reason"] = decision.reason
        routing_payload["model"] = decision.model.alias
        routing_payload["fallbacks"] = [m.alias for m in decision.fallbacks]

    selected_model = decision.model
    fallbacks = decision.fallbacks

    # Загружаем провайдера для выбранной модели
    result = await session.execute(
        select(Provider).where(Provider.id == selected_model.provider_id)
    )
    provider = result.scalar_one()

    # Enforce: применяем политику к выбранной модели
    action = _ChatAction(
        model_alias=selected_model.alias,
        model_locality=selected_model.locality,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        corpus_data_class=corpus_data_class,
        corpus_name=corpus_name,
    )
    await enforce_all(
        policy,
        action,
        session=session,
        user_id=user.id,
        workspace_id=workspace_id,
        rate_limiter=rate_limiter,
        model_cost_in=selected_model.cost_in,
        model_cost_out=selected_model.cost_out,
    )

    chat_ctx = ChatContext(
        user=user,
        policy=policy,
        messages=messages,
        model_alias=model_alias,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=stream,
        corpus_data_class=corpus_data_class,
        corpus_name=corpus_name,
        task_type=task_type,
        conversation_id=conversation_id,
        rate_limiter=rate_limiter,
        tokens_in=input_tokens,
        model_id=selected_model.id,
    )

    # Загружаем провайдеров для fallback-моделей
    fallback_with_providers: list[tuple[Model, Provider]] = []
    if fallbacks:
        provider_ids = {m.provider_id for m in fallbacks}
        prov_result = await session.execute(select(Provider).where(Provider.id.in_(provider_ids)))
        providers_by_id = {p.id: p for p in prov_result.scalars().all()}
        fallback_with_providers = [
            (m, providers_by_id[m.provider_id])
            for m in fallbacks
            if m.provider_id in providers_by_id
        ]

    return chat_ctx, selected_model, provider, fallback_with_providers


async def execute_stream(
    chat_ctx: ChatContext,
    model: Model,
    provider: Provider,
    secret_key: str,
    fallbacks: list[tuple[Model, Provider]] | None = None,
) -> AsyncGenerator[str, None]:
    """Выполняет стриминговый запрос к провайдеру. SSE-события.

    S-13: ошибка — событие error, не обрыв. [DONE] — завершение.
    Накапливает content для сохранения в finally.

    T-116b: fallback применяется только при ошибке **до** первого токена.
    Если ошибка произошла после начала генерации — fallback невозможен
    (нельзя подменить модель посреди ответа).
    """
    attempts: list[tuple[Model, Provider]] = [(model, provider)]
    if fallbacks:
        attempts.extend(fallbacks)

    try:
        for attempt_idx, (current_model, current_provider) in enumerate(attempts):
            client = ProviderClient(current_provider, secret_key)
            upstream_name = current_model.upstream_name
            got_token = False

            # T-409: DLP-детекторы перед отправкой внешнему провайдеру
            if chat_ctx.session is not None and chat_ctx.settings is not None:
                await run_detectors(
                    chat_ctx.session,
                    chat_ctx.settings,
                    chat_ctx.user,
                    current_model.id,
                    chat_ctx.conversation_id,
                    chat_ctx.messages,
                    current_provider.kind,
                )

            try:
                upstream_gen = client.stream(
                    messages=chat_ctx.messages,
                    model=upstream_name,
                    max_tokens=chat_ctx.max_tokens,
                    temperature=chat_ctx.temperature,
                )
                try:
                    async for token in upstream_gen:
                        got_token = True
                        chat_ctx.accumulated_content.append(token)
                        yield f"data: {json.dumps({'type': 'token', 'v': token})}\n\n"
                finally:
                    await upstream_gen.aclose()
                # Успешное завершение — обновляем model_id на фактическую модель
                chat_ctx.model_id = current_model.id
                chat_ctx.tokens_out = _count_tokens("".join(chat_ctx.accumulated_content))
                yield "data: [DONE]\n\n"
                return
            except Exception as exc:  # noqa: BLE001  граница системы
                err = normalize_error(exc)
                # Fallback возможен только если ни один токен не был отправлен
                if got_token or attempt_idx == len(attempts) - 1:
                    chat_ctx.error_code = err.error_code
                    yield f"data: {json.dumps({'type': 'error', 'code': err.error_code, 'message': err.reason, 'reason': err.reason, 'constraint': err.constraint, 'hint': err.hint})}\n\n"
                    break
                # Ошибка до первого токена, есть ещё fallback — пробуем следующую модель
                continue
    except GeneratorExit:
        # aclose() из _stream_with_save при disconnect клиента.
        # Считаем токены, но НЕ yield'им [DONE] — генератор закрывается.
        chat_ctx.tokens_out = _count_tokens("".join(chat_ctx.accumulated_content))
        raise
    finally:
        # Гарантируем подсчёт токенов даже при исключении
        if chat_ctx.tokens_out is None:
            chat_ctx.tokens_out = _count_tokens("".join(chat_ctx.accumulated_content))
    # [DONE] при ошибке (break из for-цикла). При успехе — уже отправлен выше.
    # При GeneratorExit — не отправляется (raise в except).
    yield "data: [DONE]\n\n"


async def execute_complete(
    chat_ctx: ChatContext,
    model: Model,
    provider: Provider,
    secret_key: str,
    fallbacks: list[tuple[Model, Provider]] | None = None,
) -> dict[str, object]:
    """Выполняет обычный (не стриминговый) запрос.

    T-116b: при ошибке основной модели (после исчерпания ретраев в with_retry)
    переключается на fallback-модели. Возвращает ответ с фактической моделью.
    """
    attempts: list[tuple[Model, Provider]] = [(model, provider)]
    if fallbacks:
        attempts.extend(fallbacks)

    last_error: OrqionError | None = None

    for current_model, current_provider in attempts:
        client = ProviderClient(current_provider, secret_key)
        upstream_name = current_model.upstream_name

        # T-409: DLP-детекторы перед отправкой внешнему провайдеру
        if chat_ctx.session is not None and chat_ctx.settings is not None:
            await run_detectors(
                chat_ctx.session,
                chat_ctx.settings,
                chat_ctx.user,
                current_model.id,
                chat_ctx.conversation_id,
                chat_ctx.messages,
                current_provider.kind,
            )

        try:
            result = await client.complete(
                messages=chat_ctx.messages,
                model=upstream_name,
                max_tokens=chat_ctx.max_tokens,
                temperature=chat_ctx.temperature,
            )
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            chat_ctx.accumulated_content.append(content)
            usage = result.get("usage", {})
            chat_ctx.tokens_out = usage.get("completion_tokens") or _count_tokens(content)
            chat_ctx.model_id = current_model.id
            return {
                "type": "complete",
                "content": content,
                "usage": {
                    "tokens_in": chat_ctx.tokens_in,
                    "tokens_out": chat_ctx.tokens_out,
                },
                "model": current_model.alias,
            }
        except Exception as exc:  # noqa: BLE001  граница системы
            last_error = normalize_error(exc)
            continue

    # Все попытки исчерпаны
    if last_error is not None:
        chat_ctx.error_code = last_error.error_code
        return {
            "type": "error",
            "code": last_error.error_code,
            "message": last_error.reason,
            "reason": last_error.reason,
            "constraint": last_error.constraint,
            "hint": last_error.hint,
        }

    return {
        "type": "error",
        "code": "unknown",
        "message": "Неизвестная ошибка",
    }


async def save_messages(
    session: AsyncSession,
    chat_ctx: ChatContext,
    model: Model,
    workspace_id: str,
    sources: list[SourceEntry] | None = None,
    max_retries: int = 2,
    base_backoff_ms: int = 50,
) -> tuple[str, str | None]:
    """Сохраняет user-сообщения и ответ модели.

    Возвращает (conversation_id, assistant_message_id).
    Если conversation_id None — создаёт новый диалог.
    Авто-заголовок: первый пользовательский message → title (обрезка 80 символов).
    assistant_message_id None, если нет содержимого (например, ошибка до стрима).
    sources: RAG-источники, сохраняются в meta assistant-сообщения (TD-5).

    BUG-007: retry при OperationalError (SQLite database is locked).
    busy_timeout=1000ms + 2 попытки (50ms backoff) → worst-case 2.05s.
    На исчерпании — raise DatabaseTemporarilyUnavailable(503) — не swallow
    (save_messages это контент пользователя, catch+warn = молчаливая потеря
    данных, BUG-010).
    """
    _log = logging.getLogger("orqion.chat")

    for attempt in range(max_retries):
        try:
            async with session.begin_nested():
                return await _save_messages_impl(
                    session, chat_ctx, model, workspace_id, sources
                )
        except OperationalError:
            if attempt < max_retries - 1:
                delay = base_backoff_ms * (2**attempt) / 1000
                _log.warning(
                    "save_messages: OperationalError, retry %d/%d after %.3fs",
                    attempt + 1,
                    max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                _log.error(
                    "save_messages: OperationalError, all %d retries exhausted",
                    max_retries,
                )

    raise DatabaseTemporarilyUnavailable(
        hint="Повторите запрос через несколько секунд",
    )


async def _save_messages_impl(
    session: AsyncSession,
    chat_ctx: ChatContext,
    model: Model,
    workspace_id: str,
    sources: list[SourceEntry] | None = None,
) -> tuple[str, str | None]:
    """Внутренняя реализация save_messages — без retry, вызывается из save_messages."""
    conversation_id = chat_ctx.conversation_id

    if conversation_id is None:
        first_user_msg = next(
            (m for m in chat_ctx.messages if m["role"] == "user"),
            None,
        )
        title = ""
        if first_user_msg is not None:
            title = first_user_msg["content"][:80]

        conv = Conversation(
            workspace_id=workspace_id,
            user_id=chat_ctx.user.id,
            title=title,
            archived=False,
        )
        session.add(conv)
        await session.flush()
        conversation_id = conv.id

    # Сохраняем user-сообщения
    for msg in chat_ctx.messages:
        if msg["role"] == "user":
            session.add(
                Message(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=msg["content"],
                    model_id=None,
                    tokens_in=None,
                    tokens_out=None,
                    meta={},
                )
            )

    # Сохраняем ответ ассистента
    full_content = "".join(chat_ctx.accumulated_content)
    assistant_message_id: str | None = None
    if full_content:
        assistant_meta: dict[str, object] = {}
        if sources:
            assistant_meta["sources"] = [
                {
                    "chunk_id": s.chunk_id,
                    "document_id": s.document_id,
                    "structural_path": s.structural_path,
                    "score": s.score,
                    "original_rank": s.original_rank,
                }
                for s in sources
            ]
        assistant_msg = Message(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            role="assistant",
            content=full_content,
            model_id=model.id,
            tokens_in=chat_ctx.tokens_in,
            tokens_out=chat_ctx.tokens_out,
            meta=assistant_meta,
        )
        session.add(assistant_msg)
        await session.flush()
        assistant_message_id = assistant_msg.id

    # Обновляем last_activity_at для retention (T-406)
    conv_result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv_for_update: Conversation | None = conv_result.scalar_one_or_none()
    if conv_for_update is not None:
        conv_for_update.last_activity_at = datetime.now(UTC)

    await session.flush()
    return conversation_id, assistant_message_id
