"""Менеджер трассировки: trace + span context manager.

arch.md ADR-14: таблицы trace и span внутри основной базы.
S-14: пакетная запись span'ов — не синхронная запись в горячем пути.
payload JSON — тела шагов, отдельный срок хранения (§5.3).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Span, Trace


@dataclass
class SpanRecord:
    """Запись о span — накапливается, пишется пачкой."""

    name: str
    started_at: float = field(default_factory=time.monotonic)
    duration_ms: int | None = None
    parent_id: str | None = None
    payload: dict[str, object] = field(default_factory=dict)


@dataclass
class TraceContext:
    """Контекст трассировки одного запроса."""

    trace_id: str
    workspace_id: str
    user_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    spans: list[SpanRecord] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    error: bool = False

    def add_span(self, record: SpanRecord) -> None:
        self.spans.append(record)


async def create_trace(
    session: AsyncSession,
    workspace_id: str,
    user_id: str | None = None,
) -> TraceContext:
    """Создаёт trace в БД, возвращает контекст."""
    trace = Trace(
        workspace_id=workspace_id,
        user_id=user_id,
        status="ok",
    )
    session.add(trace)
    await session.flush()
    return TraceContext(
        trace_id=trace.id,
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def finalize_trace(
    session: AsyncSession,
    ctx: TraceContext,
    conversation_id: str | None = None,
    message_id: str | None = None,
    error: bool = False,
) -> None:
    """Завершает trace: записывает total_ms, status, FK.

    Записывает все накопленные span'ы пачкой.
    Не возбуждает — трассировка не должна блокировать чат (S-14).
    """
    try:
        total_ms = int((time.monotonic() - ctx.started_at) * 1000)

        # Записываем span'ы пачкой
        for span_rec in ctx.spans:
            if span_rec.duration_ms is None:
                span_rec.duration_ms = int((time.monotonic() - span_rec.started_at) * 1000)
            span = Span(
                workspace_id=ctx.workspace_id,
                trace_id=ctx.trace_id,
                parent_id=span_rec.parent_id,
                name=span_rec.name,
                duration_ms=span_rec.duration_ms,
                payload=span_rec.payload,
            )
            session.add(span)

        # Обновляем trace
        trace = await session.get(Trace, ctx.trace_id)
        if trace is not None:
            trace.total_ms = total_ms
            trace.status = "error" if error else "ok"
            if conversation_id is not None:
                trace.conversation_id = conversation_id
            if message_id is not None:
                trace.message_id = message_id

        await session.commit()
    except Exception:  # noqa: BLE001  трассировка не должна блокировать чат
        import logging

        logging.getLogger("orqion.trace").warning(
            "Failed to finalize trace: trace_id=%s", ctx.trace_id
        )
        await session.rollback()


@asynccontextmanager
async def span(
    ctx: TraceContext,
    name: str,
    parent_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> AsyncIterator[SpanRecord]:
    """Контекстный менеджер для шага конвейера.

    S-14: накапливает SpanRecord, не пишет в БД сразу.
    Запись происходит в finalize_trace пачкой.
    """
    record = SpanRecord(
        name=name,
        parent_id=parent_id,
        payload=payload if payload is not None else {},
    )
    ctx.add_span(record)
    try:
        yield record
    except Exception:
        ctx.error = True
        raise
    finally:
        record.duration_ms = int((time.monotonic() - record.started_at) * 1000)
