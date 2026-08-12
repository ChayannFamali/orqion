"""Тесты источников в ответе (T-222).

Проверки:
- test_build_structural_path_document: heading_path для docs
- test_build_structural_path_code_signature: signature для code
- test_build_structural_path_code_symbol_fallback: parent.symbol fallback
- test_build_structural_path_sql: operation+tables для SQL
- test_build_structural_path_filename_only: только filename
- test_build_structural_path_empty: meta=None → пустая строка
- test_build_sources_basic: 3 фрагмента, все включены
- test_build_sources_subset: только включённые chunk_ids (truncation scenario)
- test_build_sources_oversized_skipped: oversized фрагмент не в included
- test_build_sources_preserves_inclusion_order: порядок по включению, не реранкингу
- test_build_sources_missing_chunk_skipped: chunk_id не в chunks → пропущен
- test_build_sources_missing_rerank_skipped: chunk_id не в reranked → пропущен
- test_build_sources_empty: пустой included_chunk_ids → пустой список
- test_source_entry_uses_document_id: SourceEntry.document_id, не blob_uri
"""

from __future__ import annotations

from app.db.models import Chunk
from app.rag.reranker import RerankResult
from app.rag.sources import build_sources, build_structural_path

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_chunk(
    chunk_id: str,
    document_id: str = "doc-1",
    meta: dict[str, object] | None = None,
) -> Chunk:
    chunk = Chunk(
        index_version_id="iv-1",
        document_id=document_id,
        ordinal=0,
        text="text",
        meta=meta,
    )
    chunk.id = chunk_id
    return chunk


def _make_rerank_result(chunk_id: str, score: float = 1.0, rank: int = 1) -> RerankResult:
    return RerankResult(
        chunk_id=chunk_id,
        score=score,
        text="text",
        original_rank=rank,
    )


# ---------------------------------------------------------------------------
# build_structural_path
# ---------------------------------------------------------------------------


def test_build_structural_path_document() -> None:
    """Heading_path для docs → 'guide.md › Introduction › Setup'."""
    chunk = _make_chunk(
        "c1",
        meta={
            "document_filename": "guide.md",
            "chunker": "header",
            "heading_path": ["Introduction", "Setup"],
        },
    )
    path = build_structural_path(chunk.meta, chunk)
    assert path == "guide.md › Introduction › Setup"


def test_build_structural_path_code_signature() -> None:
    """Signature для code → 'main.py › hello(name: str) -> str'."""
    chunk = _make_chunk(
        "c1",
        meta={
            "document_filename": "main.py",
            "chunker": "code",
            "symbol": "hello",
            "signature": "hello(name: str) -> str",
            "parent": "Greeter",
        },
    )
    path = build_structural_path(chunk.meta, chunk)
    assert path == "main.py › hello(name: str) -> str"


def test_build_structural_path_code_symbol_fallback() -> None:
    """Нет signature, есть symbol+parent → 'mod.py › MyClass.method_a'."""
    chunk = _make_chunk(
        "c1",
        meta={
            "document_filename": "mod.py",
            "chunker": "code",
            "symbol": "method_a",
            "parent": "MyClass",
            "signature": None,
        },
    )
    path = build_structural_path(chunk.meta, chunk)
    assert path == "mod.py › MyClass.method_a"


def test_build_structural_path_sql() -> None:
    """Operation+tables для SQL → 'migration.sql › SQL: SELECT users, orders'."""
    chunk = _make_chunk(
        "c1",
        meta={
            "document_filename": "migration.sql",
            "chunker": "sql",
            "operation": "SELECT",
            "tables": ["users", "orders"],
        },
    )
    path = build_structural_path(chunk.meta, chunk)
    assert path == "migration.sql › SQL: SELECT users, orders"


def test_build_structural_path_filename_only() -> None:
    """Только filename, нет структурных полей → 'legacy.md'."""
    chunk = _make_chunk(
        "c1",
        meta={"document_filename": "legacy.md"},
    )
    path = build_structural_path(chunk.meta, chunk)
    assert path == "legacy.md"


def test_build_structural_path_empty() -> None:
    """Meta=None → пустая строка."""
    chunk = _make_chunk("c1", meta=None)
    path = build_structural_path(chunk.meta, chunk)
    assert path == ""


# ---------------------------------------------------------------------------
# build_sources
# ---------------------------------------------------------------------------


def test_build_sources_basic() -> None:
    """3 фрагмента, все включены — 3 SourceEntry."""
    reranked = [
        _make_rerank_result("c1", score=0.9, rank=1),
        _make_rerank_result("c2", score=0.8, rank=2),
        _make_rerank_result("c3", score=0.7, rank=3),
    ]
    chunks = [
        _make_chunk("c1", document_id="doc-1", meta={"document_filename": "a.md"}),
        _make_chunk("c2", document_id="doc-2", meta={"document_filename": "b.md"}),
        _make_chunk("c3", document_id="doc-3", meta={"document_filename": "c.md"}),
    ]
    included = ["c1", "c2", "c3"]

    sources = build_sources(included, reranked, chunks)

    assert len(sources) == 3
    assert sources[0].chunk_id == "c1"
    assert sources[0].document_id == "doc-1"
    assert sources[0].structural_path == "a.md"
    assert sources[0].score == 0.9
    assert sources[0].original_rank == 1


def test_build_sources_subset() -> None:
    """Truncation: только 2 из 3 включены — sources содержат 2."""
    reranked = [
        _make_rerank_result("c1", score=0.9, rank=1),
        _make_rerank_result("c2", score=0.8, rank=2),
        _make_rerank_result("c3", score=0.7, rank=3),
    ]
    chunks = [
        _make_chunk("c1", document_id="doc-1", meta={"document_filename": "a.md"}),
        _make_chunk("c2", document_id="doc-2", meta={"document_filename": "b.md"}),
        _make_chunk("c3", document_id="doc-3", meta={"document_filename": "c.md"}),
    ]
    # c2 была oversized — не попала в контекст
    included = ["c1", "c3"]

    sources = build_sources(included, reranked, chunks)

    assert len(sources) == 2
    assert sources[0].chunk_id == "c1"
    assert sources[1].chunk_id == "c3"
    # c2 нет в источниках
    assert all(s.chunk_id != "c2" for s in sources)


def test_build_sources_preserves_inclusion_order() -> None:
    """Порядок источников — по включению в контекст, не по реранкингу.

    Сценарий: c1 (rank=1) oversized, c2 (rank=2) и c3 (rank=3) включены.
    included_chunk_ids = [c2, c3] — порядок включения, не реранкинга.
    """
    reranked = [
        _make_rerank_result("c1", score=0.9, rank=1),
        _make_rerank_result("c2", score=0.8, rank=2),
        _make_rerank_result("c3", score=0.7, rank=3),
    ]
    chunks = [
        _make_chunk("c1", document_id="doc-1", meta={"document_filename": "big.md"}),
        _make_chunk("c2", document_id="doc-2", meta={"document_filename": "small2.md"}),
        _make_chunk("c3", document_id="doc-3", meta={"document_filename": "small3.md"}),
    ]
    included = ["c2", "c3"]

    sources = build_sources(included, reranked, chunks)

    assert len(sources) == 2
    assert sources[0].chunk_id == "c2"
    assert sources[1].chunk_id == "c3"


def test_build_sources_missing_chunk_skipped() -> None:
    """Chunk_id не найден в chunks → пропущен."""
    reranked = [_make_rerank_result("c1", score=0.9, rank=1)]
    chunks: list[Chunk] = []  # нет чанков в БД
    included = ["c1"]

    sources = build_sources(included, reranked, chunks)

    assert len(sources) == 0


def test_build_sources_missing_rerank_skipped() -> None:
    """Chunk_id не найден в reranked → пропущен."""
    reranked: list[RerankResult] = []
    chunks = [_make_chunk("c1", document_id="doc-1", meta={"document_filename": "a.md"})]
    included = ["c1"]

    sources = build_sources(included, reranked, chunks)

    assert len(sources) == 0


def test_build_sources_empty() -> None:
    """Пустой included_chunk_ids → пустой список."""
    sources = build_sources([], [], [])
    assert sources == []


def test_source_entry_uses_document_id() -> None:
    """SourceEntry содержит document_id, не blob_uri."""
    chunk = _make_chunk("c1", document_id="doc-abc", meta={"document_filename": "a.md"})
    reranked = [_make_rerank_result("c1", score=0.9, rank=1)]
    included = ["c1"]

    sources = build_sources(included, reranked, [chunk])

    assert len(sources) == 1
    assert sources[0].document_id == "doc-abc"
    # SourceEntry — frozen dataclass, не имеет blob_uri поля
    assert not hasattr(sources[0], "blob_uri")
