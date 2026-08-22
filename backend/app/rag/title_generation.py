"""Генерация заголовка диалога (T-433).

arch.md §8.2: дешёвая utility-модель генерирует человекочитаемый заголовок
из первого обмена в диалоге. Выполняется через ProviderClient (T-111/T-112).

Деградация: при любой ошибке (модель не найдена, провайдер недоступен, пустой
ответ) — возврат fallback (первое сообщение, обрезанное до 80 символов),
degraded=True, error с причиной. Аналогично T-218 (query rewrite).

Вызов — fire-and-forget фоновой задачей после сохранения первого обмена,
не блокируя ответ ассистента (N-2: задержка первого токена не страдает).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Model, Provider
from app.providers.client import ProviderClient

logger = logging.getLogger("orqion.rag.title_generation")

_SYSTEM_PROMPT = (
    "Ты — генератор заголовков диалогов. Твоя задача: создать короткий "
    "заголовок (до 80 символов) для диалога на основе первого сообщения "
    "пользователя и ответа ассистента.\n"
    "Правила:\n"
    "- Верни ТОЛЬКО заголовок, без кавычек, пояснений или форматирования.\n"
    "- Сохрани язык оригинала.\n"
    "- Заголовок должен отражать суть вопроса, не быть общим («Диалог»).\n"
    "- До 80 символов — если длиннее, обрежь по слову."
)

_MAX_TITLE_LENGTH = 80


@dataclass
class TitleResult:
    """Результат генерации заголовка."""

    title: str
    degraded: bool = False
    error: str | None = None


def _clean_response(text: str) -> str:
    """Удаляет мусор по краям и обрезает до _MAX_TITLE_LENGTH по слову."""
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] in "\"'«»" and cleaned[-1] in "\"'«»":
        cleaned = cleaned[1:-1].strip()
    if len(cleaned) > _MAX_TITLE_LENGTH:
        truncated = cleaned[:_MAX_TITLE_LENGTH]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            cleaned = truncated[:last_space]
        else:
            cleaned = truncated
    return cleaned


def _fallback_title(first_user_message: str) -> str:
    """Fallback-эвристика: первое сообщение, обрезанное до 80 символов."""
    return first_user_message[:_MAX_TITLE_LENGTH]


async def _do_generate(
    first_user_message: str,
    first_assistant_message: str,
    model: Model,
    provider: Provider,
    secret_key: str,
) -> str:
    """Вызывает ProviderClient.complete с system-промптом и первым обменом."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": first_user_message},
        {"role": "assistant", "content": first_assistant_message},
    ]

    client = ProviderClient(provider, secret_key)
    result = await client.complete(
        messages=messages,
        model=model.upstream_name,
        max_tokens=64,
        temperature=0.0,
    )
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _clean_response(content)


async def maybe_generate_title(
    session: AsyncSession,
    settings: Settings,
    first_user_message: str,
    first_assistant_message: str,
    secret_key: str,
    workspace_id: str,
) -> TitleResult:
    """Генерация заголовка с конфигурацией, деградацией и fallback.

    - Если title_generation_enabled=False — fallback (обрезанное первое сообщение).
    - Если utility_model_alias пуст и rag_reformulation_model_alias пуст — fallback.
    - Если модель/провайдер не найдены или ошибка — fallback, degraded=True.
    - Заголовок не блокирует создание диалога — при любой ошибке fallback.
    """
    fallback = _fallback_title(first_user_message)

    if not settings.title_generation_enabled:
        return TitleResult(title=fallback)

    # utility_model_alias с fallback на rag_reformulation_model_alias (обратная совместимость)
    model_alias = settings.utility_model_alias or settings.rag_reformulation_model_alias
    if not model_alias:
        return TitleResult(
            title=fallback,
            degraded=True,
            error="utility_model_alias not set (and rag_reformulation_model_alias empty)",
        )

    try:
        model_result = await session.execute(
            select(Model).where(
                Model.workspace_id == workspace_id,
                Model.alias == model_alias,
                Model.enabled.is_(True),
            )
        )
        model = model_result.scalar_one_or_none()
        if model is None:
            return TitleResult(
                title=fallback,
                degraded=True,
                error=f"model '{model_alias}' not found or disabled",
            )

        provider_result = await session.execute(
            select(Provider).where(Provider.id == model.provider_id)
        )
        provider = provider_result.scalar_one_or_none()
        if provider is None or not provider.enabled:
            return TitleResult(
                title=fallback,
                degraded=True,
                error=f"provider for model '{model_alias}' not found or disabled",
            )

        generated = await _do_generate(
            first_user_message, first_assistant_message, model, provider, secret_key
        )

        if not generated:
            return TitleResult(
                title=fallback,
                degraded=True,
                error="empty response from model",
            )

        return TitleResult(title=generated)

    except Exception as exc:  # noqa: BLE001  граница системы
        error_msg = str(exc) or exc.__class__.__name__
        logger.warning("Title generation failed: %s", error_msg)
        return TitleResult(
            title=fallback,
            degraded=True,
            error=error_msg,
        )


def generate_title_background(
    session_factory: Callable[[], AsyncSession] | async_sessionmaker[AsyncSession],
    settings: Settings,
    secret_key: str,
    workspace_id: str,
    conversation_id: str,
    first_user_message: str,
    first_assistant_message: str,
    user_id: str,
    background_tasks: set[asyncio.Task[None]],
) -> None:
    """Fire-and-forget генерация заголовка фоновой задачей (T-433).

    Создаёт asyncio.Task и сохраняет референс в background_tasks (set),
    чтобы Python не собрать его GC до завершения. Открывает **свою** сессию
    (не переиспользует сессию запроса — та закрывается после отправки ответа).
    Необработанные исключения логируются структурно (conversation_id, user_id).
    """
    import asyncio

    from sqlalchemy import update

    from app.db.models import Conversation

    async def _run() -> None:
        try:
            async with session_factory() as session:
                result = await maybe_generate_title(
                    session,
                    settings,
                    first_user_message,
                    first_assistant_message,
                    secret_key,
                    workspace_id,
                )
                if result.title:
                    await session.execute(
                        update(Conversation)
                        .where(
                            Conversation.id == conversation_id,
                            Conversation.workspace_id == workspace_id,
                        )
                        .values(title=result.title)
                    )
                    await session.commit()

                logger.info(
                    "Title generation completed: degraded=%s error=%s",
                    result.degraded,
                    result.error,
                    extra={"user_id": user_id, "model_alias": settings.utility_model_alias},
                )
        except Exception:
            logger.exception(
                "Title generation background task failed: conversation_id=%s",
                conversation_id,
                extra={"user_id": user_id, "model_alias": settings.utility_model_alias},
            )

    task = asyncio.create_task(_run())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
