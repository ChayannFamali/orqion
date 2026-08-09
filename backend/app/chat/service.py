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

import fnmatch
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import tiktoken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Message, Model, Provider, User
from app.errors import NotFound
from app.policy.enforce import enforce
from app.policy.models import WILDCARD, Policy
from app.policy.rate_limiter import RateLimiter
from app.providers.client import ProviderClient
from app.providers.errors import normalize_error
from app.router.models import RouteContext
from app.router.service import load_candidate_models, load_rules, select_model

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
) -> tuple[ChatContext, Model, Provider, list[Model]]:
    """Подготовка чат-запроса: enforce, маршрутизация, загрузка модели.

    Возвращает (context, model, provider, fallbacks).
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
    decision = select_model(rules, ctx)

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
    enforce(policy, action, rate_limiter=rate_limiter, user_id=user.id)

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

    return chat_ctx, selected_model, provider, fallbacks


async def execute_stream(
    chat_ctx: ChatContext,
    model: Model,
    provider: Provider,
    secret_key: str,
) -> AsyncIterator[str]:
    """Выполняет стриминговый запрос к провайдеру. SSE-события.

    S-13: ошибка — событие error, не обрыв. [DONE] — завершение.
    Накапливает content для сохранения в finally.
    """
    client = ProviderClient(provider, secret_key)
    upstream_name = model.upstream_name

    try:
        async for token in client.stream(
            messages=chat_ctx.messages,
            model=upstream_name,
            max_tokens=chat_ctx.max_tokens,
            temperature=chat_ctx.temperature,
        ):
            chat_ctx.accumulated_content.append(token)
            yield f"data: {json.dumps({'type': 'token', 'v': token})}\n\n"
    except Exception as exc:  # noqa: BLE001  граница системы: нормализуем любую ошибку провайдера
        err = normalize_error(exc)
        chat_ctx.error_code = err.error_code
        yield f"data: {json.dumps({'type': 'error', 'code': err.error_code, 'message': err.reason})}\n\n"
    finally:
        chat_ctx.tokens_out = _count_tokens("".join(chat_ctx.accumulated_content))
        yield "data: [DONE]\n\n"


async def execute_complete(
    chat_ctx: ChatContext,
    model: Model,
    provider: Provider,
    secret_key: str,
) -> dict[str, object]:
    """Выполняет обычный (не стриминговый) запрос.

    Возвращает dict с ответом и метаданными.
    """
    client = ProviderClient(provider, secret_key)
    upstream_name = model.upstream_name

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
        return {
            "type": "complete",
            "content": content,
            "usage": {
                "tokens_in": chat_ctx.tokens_in,
                "tokens_out": chat_ctx.tokens_out,
            },
            "model": model.alias,
        }
    except Exception as exc:  # noqa: BLE001  граница системы: нормализуем любую ошибку провайдера
        err = normalize_error(exc)
        chat_ctx.error_code = err.error_code
        return {
            "type": "error",
            "code": err.error_code,
            "message": err.reason,
        }


async def save_messages(
    session: AsyncSession,
    chat_ctx: ChatContext,
    model: Model,
    workspace_id: str,
) -> tuple[str, str | None]:
    """Сохраняет user-сообщения и ответ модели.

    Возвращает (conversation_id, assistant_message_id).
    Если conversation_id None — создаёт новый диалог.
    Авто-заголовок: первый пользовательский message → title (обрезка 80 символов).
    assistant_message_id None, если нет содержимого (например, ошибка до стрима).
    """
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
        assistant_msg = Message(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            role="assistant",
            content=full_content,
            model_id=model.id,
            tokens_in=chat_ctx.tokens_in,
            tokens_out=chat_ctx.tokens_out,
            meta={},
        )
        session.add(assistant_msg)
        await session.flush()
        assistant_message_id = assistant_msg.id

    await session.commit()
    return conversation_id, assistant_message_id
