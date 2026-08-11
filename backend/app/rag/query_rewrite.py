"""Переформулировка запроса (T-218).

arch.md §8.2 шаг 2: дешёвая модель превращает вопрос, зависящий от контекста
диалога, в самодостаточный. Выполняется через существующий providers-адаптер
(T-111/T-112), без новых ML-зависимостей.

Деградация: при любой ошибке (модель не найдена, провайдер недоступен, пустой
ответ) — возврат исходного запроса, degraded=True, error с причиной.
Аналогично T-217 (реранкинг).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Model, Provider
from app.providers.client import ProviderClient
from app.trace.service import TraceContext, span

logger = logging.getLogger("orqion.rag.query_rewrite")

_SYSTEM_PROMPT = (
    "Ты — переформулировщик запросов. Твоя задача: переписать последний вопрос "
    "пользователя так, чтобы он был самодостаточным — понятным без контекста "
    "предыдущего диалога.\n"
    "Правила:\n"
    "- Верни ТОЛЬКО переформулированный вопрос, без вступительных фраз, "
    "пояснений, кавычек или форматирования.\n"
    "- Сохрани язык оригинала.\n"
    "- Не добавляй информацию, которой нет в вопросе или истории диалога.\n"
    "- Если последний вопрос уже самодостаточен, верни его дословно."
)

_MAX_CONTEXT_MESSAGES = 10


@dataclass
class RewriteResult:
    """Результат переформулировки."""

    query: str
    degraded: bool = False
    error: str | None = None


def _clean_response(text: str) -> str:
    """Удаляет типичный мусор по краям ответа модели.

    Снимает whitespace и парные кавычки/апострофы, если модель обернула ответ.
    """
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] in "\"'«»" and cleaned[-1] in "\"'«»":
        cleaned = cleaned[1:-1].strip()
    return cleaned


async def _do_rewrite(
    messages: list[dict[str, str]],
    model: Model,
    provider: Provider,
    secret_key: str,
) -> str:
    """Вызывает ProviderClient.complete с system-промптом и историей диалога.

    Возвращает очищенный текст переформулированного запроса.
    """
    system_msg: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]

    history = (
        messages[-_MAX_CONTEXT_MESSAGES:] if len(messages) > _MAX_CONTEXT_MESSAGES else messages
    )

    client = ProviderClient(provider, secret_key)
    result = await client.complete(
        messages=system_msg + history,
        model=model.upstream_name,
        max_tokens=512,
        temperature=0.0,
    )
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _clean_response(content)


async def maybe_rewrite_query(
    session: AsyncSession,
    settings: Settings,
    messages: list[dict[str, str]],
    secret_key: str,
    workspace_id: str,
    trace_ctx: TraceContext | None = None,
) -> RewriteResult:
    """Переформулировка запроса с конфигурацией, деградацией и трассировкой.

    - Если переформулировка отключена в конфиге — возврат исходного запроса.
    - Если нет истории (1 сообщение) — запрос уже самодостаточен.
    - Если model_alias не задан или модель/провайдер не найдены — деградация.
    - При любой ошибке — деградация: возврат исходного запроса.

    Трассировка: span("rewrite") с payload {original_query, rewritten_query,
    model_alias, degraded, error}.
    """
    last_user_msg = next(
        (m for m in reversed(messages) if m["role"] == "user"),
        None,
    )
    if last_user_msg is None:
        return RewriteResult(query="", degraded=True, error="no user message")

    original_query = last_user_msg["content"]

    if not settings.rag_query_reformulation_enabled:
        return RewriteResult(query=original_query)

    if len(messages) <= 1:
        return RewriteResult(query=original_query)

    model_alias = settings.rag_reformulation_model_alias
    if not model_alias:
        return RewriteResult(
            query=original_query,
            degraded=True,
            error="rag_reformulation_model_alias not set",
        )

    span_payload: dict[str, object] = {
        "original_query": original_query,
        "model_alias": model_alias,
        "degraded": False,
        "error": None,
    }

    result = RewriteResult(query=original_query)

    async with span(trace_ctx, "rewrite", payload=span_payload) if trace_ctx else _NullSpan():
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
                result = RewriteResult(
                    query=original_query,
                    degraded=True,
                    error=f"model '{model_alias}' not found or disabled",
                )
                return result

            provider_result = await session.execute(
                select(Provider).where(Provider.id == model.provider_id)
            )
            provider = provider_result.scalar_one_or_none()
            if provider is None or not provider.enabled:
                result = RewriteResult(
                    query=original_query,
                    degraded=True,
                    error=f"provider for model '{model_alias}' not found or disabled",
                )
                return result

            rewritten = await _do_rewrite(messages, model, provider, secret_key)

            if not rewritten:
                result = RewriteResult(
                    query=original_query,
                    degraded=True,
                    error="empty response from model",
                )
                return result

            result = RewriteResult(query=rewritten)
            return result

        except Exception as exc:  # noqa: BLE001  граница системы
            error_msg = str(exc) or exc.__class__.__name__
            result = RewriteResult(
                query=original_query,
                degraded=True,
                error=error_msg,
            )
            logger.warning("Query reformulation failed: %s", error_msg)
            return result
        finally:
            span_payload["rewritten_query"] = result.query
            span_payload["degraded"] = result.degraded
            span_payload["error"] = result.error

    return result


class _NullSpan:
    """No-op async context manager когда trace_ctx не передан.

    Для юнит-тестов и случаев, когда трассировка не нужна.
    """

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None
