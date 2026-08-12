"""Тесты сборки контекста (T-219).

Проверки:
- test_basic_context: 3 фрагмента, все влезают, порядок сохранён
- test_structural_path_document: heading_path в заголовке фрагмента
- test_structural_path_code: symbol/signature в заголовке фрагмента
- test_structural_path_sql: operation/tables в заголовке фрагмента
- test_truncation_drops_least_relevant: превышение → выброс целых фрагментов
- test_truncation_no_single_fragment_fits: лимит 0 → только системная инструкция + запрос
- test_empty_reranked: пустой список → только системная инструкция + запрос
- test_system_prompt_present: инструкция «отвечать только по материалу» в начале
- test_query_appended: переформулированный запрос в конце контекста
- test_token_count_uses_tiktoken: проверка через tiktoken, не эвристика
- test_trace_span_payload: span с правильным payload
- test_missing_structural_fields_falls_back_to_filename: graceful fallback (T-214b)
"""

from __future__ import annotations

from app.db.models import Chunk, Workspace
from app.rag.context_builder import (
    _count_tokens,
    build_context,
)
from app.rag.reranker import RerankResult
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_rerank_result(chunk_id: str, text: str, rank: int = 1) -> RerankResult:
    return RerankResult(
        chunk_id=chunk_id,
        score=1.0 / (rank + 1),
        text=text,
        original_rank=rank,
    )


def _make_chunk(
    chunk_id: str,
    text: str,
    meta: dict[str, object] | None = None,
) -> Chunk:
    chunk = Chunk(
        index_version_id="iv-1",
        document_id="doc-1",
        ordinal=0,
        text=text,
        meta=meta,
    )
    chunk.id = chunk_id
    return chunk


def _large_text(token_target: int) -> str:
    """Создаёт текст, занимающий ~token_target токенов."""
    word = "test "
    tokens_per_word = _count_tokens(word)
    count = token_target // tokens_per_word + 1
    return word * count


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


async def test_basic_context() -> None:
    """3 фрагмента, все влезают, порядок сохранён."""
    reranked = [
        _make_rerank_result("c1", "Content one", rank=1),
        _make_rerank_result("c2", "Content two", rank=2),
        _make_rerank_result("c3", "Content three", rank=3),
    ]
    chunks = [
        _make_chunk("c1", "Content one", {"document_filename": "doc1.md", "chunker": "header"}),
        _make_chunk("c2", "Content two", {"document_filename": "doc2.md", "chunker": "header"}),
        _make_chunk("c3", "Content three", {"document_filename": "doc3.md", "chunker": "header"}),
    ]

    result = await build_context(reranked, chunks, max_tokens=10000, query="test query")

    assert result.fragments_used == 3
    assert result.truncated is False
    assert "Content one" in result.context
    assert "Content two" in result.context
    assert "Content three" in result.context
    # Порядок сохранён
    pos1 = result.context.index("Content one")
    pos2 = result.context.index("Content two")
    pos3 = result.context.index("Content three")
    assert pos1 < pos2 < pos3


async def test_structural_path_document() -> None:
    """heading_path в заголовке фрагмента."""
    reranked = [_make_rerank_result("c1", "Document text", rank=1)]
    chunks = [
        _make_chunk(
            "c1",
            "Document text",
            {
                "document_filename": "guide.md",
                "chunker": "header",
                "heading_path": ["Introduction", "Setup"],
            },
        ),
    ]

    result = await build_context(reranked, chunks, max_tokens=10000, query="how to setup?")

    assert "guide.md" in result.context
    assert "Introduction" in result.context
    assert "Setup" in result.context
    assert "›" in result.context


async def test_structural_path_code() -> None:
    """symbol/signature в заголовке фрагмента."""
    reranked = [_make_rerank_result("c1", "def hello(): pass", rank=1)]
    chunks = [
        _make_chunk(
            "c1",
            "def hello(): pass",
            {
                "document_filename": "main.py",
                "chunker": "code",
                "symbol": "hello",
                "parent": "Greeter",
                "signature": "hello(name: str) -> str",
            },
        ),
    ]

    result = await build_context(reranked, chunks, max_tokens=10000, query="how does hello work?")

    assert "main.py" in result.context
    assert "hello(name: str) -> str" in result.context


async def test_structural_path_code_symbol_fallback() -> None:
    """Если signature нет, но есть symbol+parent → parent.symbol в заголовке."""
    reranked = [_make_rerank_result("c1", "x = 1", rank=1)]
    chunks = [
        _make_chunk(
            "c1",
            "x = 1",
            {
                "document_filename": "mod.py",
                "chunker": "code",
                "symbol": "method_a",
                "parent": "MyClass",
                "signature": None,
            },
        ),
    ]

    result = await build_context(reranked, chunks, max_tokens=10000, query="what is method_a?")

    assert "MyClass.method_a" in result.context


async def test_structural_path_sql() -> None:
    """operation/tables в заголовке фрагмента."""
    reranked = [_make_rerank_result("c1", "SELECT * FROM users", rank=1)]
    chunks = [
        _make_chunk(
            "c1",
            "SELECT * FROM users",
            {
                "document_filename": "migration.sql",
                "chunker": "sql",
                "operation": "SELECT",
                "tables": ["users", "orders"],
            },
        ),
    ]

    result = await build_context(reranked, chunks, max_tokens=10000, query="query users")

    assert "migration.sql" in result.context
    assert "SQL: SELECT users, orders" in result.context


async def test_truncation_drops_least_relevant() -> None:
    """8 фрагментов, лимит позволяет только 3 → 3 наиболее релевантных, truncated=True."""
    reranked = [_make_rerank_result(f"c{i}", _large_text(100), rank=i) for i in range(1, 9)]
    chunks = [
        _make_chunk(
            f"c{i}", _large_text(100), {"document_filename": f"doc{i}.md", "chunker": "header"}
        )
        for i in range(1, 9)
    ]

    # Каждый фрагмент ~67 токенов (header + text). Системная инструкция 50 + запрос 1.
    # 3 фрагмента: 50+1+3*67=252, 4-й: 50+1+4*67=319
    # Лимит 260 — влезает 3, 4-й не влезает
    result = await build_context(reranked, chunks, max_tokens=260, query="test")

    assert result.fragments_used == 3
    assert result.truncated is True
    # Первые 3 фрагмента (наиболее релевантные) — в контексте
    assert "Фрагмент 1" in result.context
    assert "Фрагмент 2" in result.context
    assert "Фрагмент 3" in result.context
    # 4-й — нет
    assert "Фрагмент 4" not in result.context


async def test_truncation_no_single_fragment_fits() -> None:
    """Лимит 0 → только системная инструкция + запрос, fragments_used=0."""
    reranked = [_make_rerank_result("c1", "Some content", rank=1)]
    chunks = [_make_chunk("c1", "Some content", {"document_filename": "doc.md"})]

    result = await build_context(reranked, chunks, max_tokens=1, query="q")

    assert result.fragments_used == 0
    assert result.truncated is True


async def test_empty_reranked() -> None:
    """Пустой список → только системная инструкция + запрос."""
    result = await build_context([], [], max_tokens=10000, query="test query")

    assert result.fragments_used == 0
    assert result.truncated is False
    assert "предоставленные ниже фрагменты" in result.context
    assert "test query" in result.context


async def test_system_prompt_present() -> None:
    """Инструкция «отвечать только по материалу» в начале контекста."""
    reranked = [_make_rerank_result("c1", "Content", rank=1)]
    chunks = [_make_chunk("c1", "Content", {"document_filename": "doc.md"})]

    result = await build_context(reranked, chunks, max_tokens=10000, query="test")

    assert result.context.startswith("Отвечай на вопрос")
    assert "предоставленные ниже фрагменты" in result.context


async def test_query_appended() -> None:
    """Переформулированный запрос в конце контекста."""
    reranked = [_make_rerank_result("c1", "Content", rank=1)]
    chunks = [_make_chunk("c1", "Content", {"document_filename": "doc.md"})]

    result = await build_context(reranked, chunks, max_tokens=10000, query="Как настроить orqion?")

    assert result.context.endswith("Как настроить orqion?")


async def test_token_count_uses_tiktoken() -> None:
    """Проверка что токены считаются через tiktoken, а не эвристикой."""
    text = "Hello, world! Привет, мир!"
    token_count = _count_tokens(text)

    # tiktoken даёт конкретное число, не len(text) и не len(text.split())
    assert token_count != len(text)
    assert token_count != len(text.split())
    # Точное значение для cl100k_base
    import tiktoken

    encoder = tiktoken.get_encoding("cl100k_base")
    assert token_count == len(encoder.encode(text))


async def test_trace_span_payload(db_session: AsyncSession) -> None:
    """Span 'build_context' с правильным payload."""
    from app.trace.service import create_trace

    ws = Workspace(name="test")
    db_session.add(ws)
    await db_session.flush()
    workspace_id = ws.id

    trace_ctx = await create_trace(db_session, workspace_id)

    reranked = [
        _make_rerank_result("c1", "Content one", rank=1),
        _make_rerank_result("c2", "Content two", rank=2),
    ]
    chunks = [
        _make_chunk("c1", "Content one", {"document_filename": "doc1.md"}),
        _make_chunk("c2", "Content two", {"document_filename": "doc2.md"}),
    ]

    result = await build_context(
        reranked, chunks, max_tokens=10000, query="test", trace_ctx=trace_ctx
    )

    assert result.fragments_used == 2
    assert len(trace_ctx.spans) == 1
    span_rec = trace_ctx.spans[0]
    assert span_rec.name == "build_context"
    assert span_rec.payload["fragments_used"] == 2
    assert span_rec.payload["max_tokens"] == 10000
    assert span_rec.payload["truncated"] is False
    assert isinstance(span_rec.payload["tokens_used"], int)
    assert span_rec.payload["fragments_skipped_oversized"] == 0


async def test_oversized_top_fragment_skipped_smaller_ones_included() -> None:
    """Oversized top-фрагмент пропускается, smaller последующие включаются.

    Сценарий: 1-й фрагмент (rank=1, наиболее релевантный) — большой, не влезает.
    2-й и 3-й — маленькие, влезают. break остановил бы на 1-м, continue — пропускает
    и добавляет 2-й и 3-й.
    """
    reranked = [
        _make_rerank_result("c1", _large_text(500), rank=1),
        _make_rerank_result("c2", "Small content two", rank=2),
        _make_rerank_result("c3", "Small content three", rank=3),
    ]
    chunks = [
        _make_chunk("c1", _large_text(500), {"document_filename": "big.md"}),
        _make_chunk("c2", "Small content two", {"document_filename": "small2.md"}),
        _make_chunk("c3", "Small content three", {"document_filename": "small3.md"}),
    ]

    # Лимит: системная инструкция (50) + запрос (1) = 51. Большой фрагмент ~500 — не влезет.
    # Маленькие ~15-20 токенов каждый — влезут.
    result = await build_context(reranked, chunks, max_tokens=200, query="test")

    assert result.fragments_used == 2
    assert result.fragments_skipped_oversized == 1
    assert result.truncated is True
    assert "Small content two" in result.context
    assert "Small content three" in result.context
    # Большой фрагмент исключён
    assert "test test test" not in result.context


async def test_missing_structural_fields_falls_back_to_filename() -> None:
    """Graceful fallback: если структурных полей нет — заголовок из document_filename."""
    reranked = [_make_rerank_result("c1", "Some text", rank=1)]
    chunks = [
        _make_chunk(
            "c1",
            "Some text",
            {"document_filename": "legacy.md"},  # Нет chunker, нет heading_path/symbol
        ),
    ]

    result = await build_context(reranked, chunks, max_tokens=10000, query="test")

    assert "legacy.md" in result.context
    assert "Фрагмент 1" in result.context
    assert "Some text" in result.context
    assert result.fragments_used == 1


async def test_missing_all_meta_falls_back_to_index() -> None:
    """Graceful fallback: если meta=None — заголовок только с индексом."""
    reranked = [_make_rerank_result("c1", "Some text", rank=1)]
    chunks = [_make_chunk("c1", "Some text", meta=None)]

    result = await build_context(reranked, chunks, max_tokens=10000, query="test")

    assert "Фрагмент 1" in result.context
    assert "Some text" in result.context
    assert result.fragments_used == 1


async def test_chunk_not_in_map_skipped() -> None:
    """Если chunk_id из RerankResult не найден в chunks — фрагмент пропускается."""
    reranked = [
        _make_rerank_result("c1", "Content one", rank=1),
        _make_rerank_result("c-missing", "Missing content", rank=2),
    ]
    chunks = [_make_chunk("c1", "Content one", {"document_filename": "doc1.md"})]

    result = await build_context(reranked, chunks, max_tokens=10000, query="test")

    assert result.fragments_used == 1
    assert "Content one" in result.context
    assert "Missing content" not in result.context


async def test_included_chunk_ids_basic() -> None:
    """3 фрагмента, все влезают — included_chunk_ids содержит все 3."""
    reranked = [
        _make_rerank_result("c1", "Content one", rank=1),
        _make_rerank_result("c2", "Content two", rank=2),
        _make_rerank_result("c3", "Content three", rank=3),
    ]
    chunks = [
        _make_chunk("c1", "Content one", {"document_filename": "doc1.md"}),
        _make_chunk("c2", "Content two", {"document_filename": "doc2.md"}),
        _make_chunk("c3", "Content three", {"document_filename": "doc3.md"}),
    ]

    result = await build_context(reranked, chunks, max_tokens=10000, query="test")

    assert result.included_chunk_ids == ["c1", "c2", "c3"]


async def test_included_chunk_ids_oversized_skipped() -> None:
    """Oversized фрагмент пропущен — его chunk_id не в included_chunk_ids.

    Сценарий: c1 (rank=1) — большой, не влезает. c2, c3 — маленькие, влезают.
    included_chunk_ids = [c2, c3], без c1.
    """
    reranked = [
        _make_rerank_result("c1", _large_text(500), rank=1),
        _make_rerank_result("c2", "Small content two", rank=2),
        _make_rerank_result("c3", "Small content three", rank=3),
    ]
    chunks = [
        _make_chunk("c1", _large_text(500), {"document_filename": "big.md"}),
        _make_chunk("c2", "Small content two", {"document_filename": "small2.md"}),
        _make_chunk("c3", "Small content three", {"document_filename": "small3.md"}),
    ]

    result = await build_context(reranked, chunks, max_tokens=200, query="test")

    assert result.fragments_used == 2
    assert "c1" not in result.included_chunk_ids
    assert result.included_chunk_ids == ["c2", "c3"]


async def test_included_chunk_ids_empty() -> None:
    """Пустой reranked → included_chunk_ids пустой."""
    result = await build_context([], [], max_tokens=10000, query="test")

    assert result.included_chunk_ids == []
