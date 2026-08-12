"""Источники в ответе (T-222).

arch.md §8.2: событие sources — документ, раздел или символ, релевантность,
ссылка на оригинал. SourceEntry использует document_id (не blob_uri —
внутренний storage-идентификатор неприменим для клиента).

build_structural_path извлечена из context_builder._build_fragment_header
для переиспользования: заголовки фрагментов и источники используют
одну и ту же логику построения структурного пути.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Chunk
from app.rag.reranker import RerankResult


def build_structural_path(
    chunk_meta: dict[str, object] | None,
    chunk: Chunk,
) -> str:
    """Строит структурный путь из метаданных чанка (T-214b).

    Возвращает человекочитаемый путь:
    - 'guide.md › Introduction › Setup' (docs, heading_path)
    - 'main.py › hello(name: str) -> str' (code, signature)
    - 'main.py › Greeter.method_a' (code, symbol+parent fallback)
    - 'migration.sql › SQL: SELECT users, orders' (SQL)
    - 'legacy.md' (fallback — только filename)
    - '' (нет meta вообще)

    Используется в:
    - context_builder._build_fragment_header (заголовки фрагментов)
    - sources.build_sources (structural_path в SourceEntry)
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
            return f"{filename} › {' › '.join(parts)}"
        return filename

    if parts:
        return " › ".join(parts)

    return ""


@dataclass(frozen=True)
class SourceEntry:
    """Источник в ответе RAG (T-222).

    chunk_id — UUID чанка (String(36)).
    document_id — UUID документа (String(36)), НЕ blob_uri.
    structural_path — человекочитаемый путь из метаданных чанка.
    score — оценка реранкера (или RRF-скор в degraded mode).
    original_rank — позиция в merged до реранкинга, 1-based.
    """

    chunk_id: str
    document_id: str
    structural_path: str
    score: float
    original_rank: int


def build_sources(
    included_chunk_ids: list[str],
    reranked: list[RerankResult],
    chunks: list[Chunk],
) -> list[SourceEntry]:
    """Строит список источников из включённых фрагментов.

    Принимает included_chunk_ids из ContextOutput — только те фрагменты,
    которые реально попали в контекст (после truncation/oversized skip).

    Args:
        included_chunk_ids: chunk_id, попавшие в контекст (в порядке включения).
        reranked: результаты реранкинга (для score и original_rank).
        chunks: Chunk из БД (для document_id и meta).

    Returns:
        Список SourceEntry в порядке включения фрагментов в контекст.
    """
    chunk_map: dict[str, Chunk] = {c.id: c for c in chunks}
    rerank_map: dict[str, RerankResult] = {r.chunk_id: r for r in reranked}

    sources: list[SourceEntry] = []
    for chunk_id in included_chunk_ids:
        chunk = chunk_map.get(chunk_id)
        rerank_result = rerank_map.get(chunk_id)
        if chunk is None or rerank_result is None:
            continue
        structural_path = build_structural_path(chunk.meta, chunk)
        sources.append(
            SourceEntry(
                chunk_id=chunk_id,
                document_id=chunk.document_id,
                structural_path=structural_path,
                score=rerank_result.score,
                original_rank=rerank_result.original_rank,
            )
        )
    return sources
