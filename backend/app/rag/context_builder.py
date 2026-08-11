"""Сборка контекста из реранкнутых фрагментов (T-219).

arch.md §8.2 шаг 4: из top-K реранкнутых фрагментов собирается текстовый
контекст для целевой модели. Структурные пути (heading_path, сигнатуры кода,
операции SQL) попадают в заголовки фрагментов. Превышение лимита — выброс
наименее релевантных целиком, без обрезки текста.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import tiktoken

from app.db.models import Chunk
from app.rag.reranker import RerankResult
from app.trace.service import TraceContext, span

logger = logging.getLogger("orqion.rag.context_builder")

_SYSTEM_PROMPT = (
    "Отвечай на вопрос, используя только предоставленные ниже фрагменты.\n"
    "Если ответа нет в материале, скажи об этом прямо."
)

_ENCODER: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def _count_tokens(text: str) -> int:
    if not text:
        return 1
    return len(_get_encoder().encode(text))


def _build_fragment_header(
    chunk_meta: dict[str, object] | None,
    chunk: Chunk,
    index: int,
) -> str:
    """Строит заголовок фрагмента из структурных метаданных.

    Гraceful fallback (T-214b): если структурных полей нет в meta
    (старые index_version до T-214b) — заголовок из document_filename.
    """
    meta = chunk_meta or {}
    filename = str(meta.get("document_filename", ""))
    chunker = str(meta.get("chunker", ""))

    parts: list[str] = []

    if chunker == "code":
        symbol = meta.get("symbol")
        signature = meta.get("signature")
        parent = meta.get("parent")
        if signature:
            parts.append(str(signature))
        elif symbol:
            if parent:
                parts.append(f"{parent}.{symbol}")
            else:
                parts.append(str(symbol))
    elif chunker == "sql":
        operation = meta.get("operation")
        tables = meta.get("tables")
        if operation and tables:
            tables_str = (
                ", ".join(str(t) for t in tables) if isinstance(tables, list) else str(tables)
            )
            parts.append(f"SQL: {operation} {tables_str}")
        elif operation:
            parts.append(f"SQL: {operation}")
    else:
        heading_path = meta.get("heading_path")
        if heading_path and isinstance(heading_path, list):
            parts.append(" › ".join(str(h) for h in heading_path))

    if filename:
        if parts:
            return f"── Фрагмент {index}: {filename} › {' › '.join(parts)} ──"
        return f"── Фрагмент {index}: {filename} ──"

    if parts:
        return f"── Фрагмент {index}: {' › '.join(parts)} ──"

    return f"── Фрагмент {index} ──"


@dataclass
class ContextOutput:
    """Результат сборки контекста."""

    context: str
    fragments_used: int
    tokens_used: int
    truncated: bool
    fragments_skipped_oversized: int = 0


async def build_context(
    reranked: list[RerankResult],
    chunks: list[Chunk],
    max_tokens: int,
    query: str,
    trace_ctx: TraceContext | None = None,
) -> ContextOutput:
    """Собирает текстовый контекст из реранкнутых фрагментов.

    - Фрагменты добавляются по порядку реранкинга (от наиболее релевантного).
    - Превышение лимита → выброс фрагмента целиком, без обрезки текста.
    - tiktoken (cl100k_base) для подсчёта токенов (T-107).
    - Структурные пути из Chunk.meta (T-214b) в заголовках фрагментов.
    - Graceful fallback: если полей нет — заголовок из document_filename.
    - Трассировка: span("build_context") с payload.
    """
    chunk_map: dict[str, Chunk] = {c.id: c for c in chunks}

    system_tokens = _count_tokens(_SYSTEM_PROMPT)
    query_tokens = _count_tokens(query)

    fragments_text: list[str] = []
    tokens_used = system_tokens + query_tokens
    fragments_used = 0
    fragments_skipped_oversized = 0

    span_payload: dict[str, object] = {
        "fragments_used": 0,
        "tokens_used": 0,
        "max_tokens": max_tokens,
        "truncated": False,
    }

    async with span(trace_ctx, "build_context", payload=span_payload) if trace_ctx else _NullSpan():
        for i, result in enumerate(reranked):
            chunk = chunk_map.get(result.chunk_id)
            if chunk is None:
                continue

            header = _build_fragment_header(chunk.meta, chunk, i + 1)
            fragment = f"{header}\n{result.text}"

            fragment_tokens = _count_tokens(fragment)
            if tokens_used + fragment_tokens > max_tokens:
                fragments_skipped_oversized += 1
                continue

            fragments_text.append(fragment)
            tokens_used += fragment_tokens
            fragments_used += 1

        context_parts: list[str] = [_SYSTEM_PROMPT]
        if fragments_text:
            context_parts.append("\n\n".join(fragments_text))
        context_parts.append(query)
        context = "\n\n".join(context_parts)

        truncated = fragments_used < len(reranked)
        span_payload["fragments_used"] = fragments_used
        span_payload["tokens_used"] = tokens_used
        span_payload["truncated"] = truncated
        span_payload["fragments_skipped_oversized"] = fragments_skipped_oversized

        return ContextOutput(
            context=context,
            fragments_used=fragments_used,
            tokens_used=tokens_used,
            truncated=truncated,
            fragments_skipped_oversized=fragments_skipped_oversized,
        )

    return ContextOutput(
        context=f"{_SYSTEM_PROMPT}\n\n{query}",
        fragments_used=0,
        tokens_used=system_tokens + query_tokens,
        truncated=False,
    )


class _NullSpan:
    """No-op async context manager когда trace_ctx не передан."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None
