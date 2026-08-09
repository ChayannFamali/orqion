"""Учёт потребления: запись usage_event, расчёт стоимости.

arch.md §5.1: usage_event(id, workspace_id, user_id, model_id, conversation_id,
ts, tokens_in, tokens_out, cost, latency_ms, status, error_code).
Содержимое запросов и ответов НЕ пишется (AGENTS.md §5.2, §14).
Учёт ведётся и при ошибке, и при обрыве (S-13).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageEvent


@dataclass(frozen=True)
class UsageRecord:
    """Данные для записи usage_event."""

    user_id: str | None
    model_id: str | None
    conversation_id: str | None
    message_id: str | None
    tokens_in: int | None
    tokens_out: int | None
    cost: float | None
    latency_ms: int | None
    status: str
    error_code: str | None = None


def calculate_cost(
    tokens_in: int | None,
    tokens_out: int | None,
    cost_in: float | None,
    cost_out: float | None,
) -> float | None:
    """Расчёт стоимости по параметрам модели на момент запроса.

    cost_in/cost_out — за 1M токенов (arch.md §5.1 Model).
    Возвращает None, если оба rate неизвестны.
    """
    if cost_in is None and cost_out is None:
        return None
    in_cost = (tokens_in or 0) / 1_000_000 * (cost_in or 0)
    out_cost = (tokens_out or 0) / 1_000_000 * (cost_out or 0)
    total = in_cost + out_cost
    return round(total, 6) if total > 0 else 0.0


async def record_usage(
    session: AsyncSession,
    workspace_id: str,
    record: UsageRecord,
) -> str:
    """Записывает usage_event. Возвращает id записи.

    Не возбуждает исключения при ошибке записи — логирует WARN.
    Учёт не должен блокировать основной поток (S-13).
    """
    try:
        event = UsageEvent(
            workspace_id=workspace_id,
            user_id=record.user_id,
            model_id=record.model_id,
            conversation_id=record.conversation_id,
            message_id=record.message_id,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            cost=record.cost,
            latency_ms=record.latency_ms,
            status=record.status,
            error_code=record.error_code,
        )
        session.add(event)
        await session.flush()
        await session.commit()
        return event.id
    except Exception:  # noqa: BLE001  учёт не должен блокировать чат
        import logging

        logging.getLogger("orqion.usage").warning(
            "Failed to record usage_event: user=%s model=%s status=%s",
            record.user_id,
            record.model_id,
            record.status,
        )
        await session.rollback()
        return ""
