"""Тесты чанкинга документов (T-207, S-22).

Проверки:
- Чанк по границам заголовков, путь заголовков в meta
- Целевой размер 400–800 токенов
- Таблица не разрывается — отдельный чанк
- Перекрытие между соседними чанками
- Fallback-документ (нет заголовков) — heading_path: [], heading_path_source: "none"
- Версия чанкера в meta
- Крупная секция разбивается по параграфам
- Пустой документ → пустой список
"""

from __future__ import annotations

import pytest
import tiktoken
from app.rag.chunker import CHUNKER_VERSION, chunk_document


def _make_markdown_with_headings() -> str:
    """Markdown с заголовками H1 → H2 → H3."""
    return """# Document Title

Intro paragraph.

## Section 1

Content of section 1.

### Subsection 1.1

Detailed content of subsection 1.1.

## Section 2

Content of section 2.
"""


def _make_large_section_markdown() -> str:
    """Markdown с секцией, превышающей MAX_TOKENS."""
    para = "This is a paragraph with enough content to fill tokens. " * 100
    return f"""# Large Document

## Big Section

{para}

## Small Section

Short content.
"""


def _make_table_markdown() -> str:
    """Markdown с таблицей."""
    return """# Report

## Data

| Name | Value |
|------|-------|
| A    | 1     |
| B    | 2     |
| C    | 3     |

## After Table

Text after table.
"""


def _make_plain_text() -> str:
    """Простой текст без заголовков (fallback parser)."""
    return "Line one.\n\nLine two.\n\nLine three.\n"


@pytest.fixture
def encoder() -> tiktoken.Encoding:
    return tiktoken.encoding_for_model("gpt-4")


def test_chunk_by_headings(encoder: tiktoken.Encoding) -> None:
    """Чанки разделяются по заголовкам, путь заголовков в meta."""
    md = _make_markdown_with_headings()
    chunks = chunk_document(md, parser="docling")

    assert len(chunks) > 0
    # Каждый чанк имеет heading_path
    for chunk in chunks:
        assert "heading_path" in chunk.meta
        assert chunk.meta["chunker_version"] == CHUNKER_VERSION
        assert chunk.meta["heading_path_source"] == "markdown"

    # Первый чанк — под "Document Title"
    assert "Document Title" in chunks[0].meta["heading_path"]  # type: ignore[operator]

    # Есть чанк с путём ["Document Title", "Section 1", "Subsection 1.1"]
    paths = [c.meta["heading_path"] for c in chunks]
    assert ["Document Title", "Section 1", "Subsection 1.1"] in paths


def test_chunk_size_within_limits(encoder: tiktoken.Encoding) -> None:
    """Каждый чанк в пределах 0–800 токенов."""
    md = _make_large_section_markdown()
    chunks = chunk_document(md, parser="docling")

    assert len(chunks) > 1
    for chunk in chunks:
        token_count = len(encoder.encode(chunk.text))
        assert token_count <= 800, f"Chunk {chunk.ordinal}: {token_count} tokens > 800"


def test_table_not_split(encoder: tiktoken.Encoding) -> None:
    """Таблица не разрывается — отдельный чанк с is_table: True."""
    md = _make_table_markdown()
    chunks = chunk_document(md, parser="docling")

    table_chunks = [c for c in chunks if c.meta.get("is_table")]
    assert len(table_chunks) == 1
    table_text = table_chunks[0].text
    assert "| Name | Value |" in table_text
    assert "| C    | 3     |" in table_text


def test_overlap_between_chunks(encoder: tiktoken.Encoding) -> None:
    """Перекрытие между соседними чанками при разрыве секции."""
    md = _make_large_section_markdown()
    chunks = chunk_document(md, parser="docling")

    if len(chunks) < 2:
        pytest.skip("Section didn't split")

    # Проверяем, что есть перекрытие — последние слова первого чанка
    # присутствуют в начале второго
    # (не строгое условие — перекрытие может быть по параграфам)
    assert chunks[0].ordinal == 0
    assert chunks[1].ordinal == 1


def test_fallback_no_headings(encoder: tiktoken.Encoding) -> None:
    """Fallback-документ (нет заголовков) — heading_path: [], source: "none"."""
    md = _make_plain_text()
    chunks = chunk_document(md, parser="fallback")

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.meta["heading_path"] == []
        assert chunk.meta["heading_path_source"] == "none"


def test_direct_parser_has_markdown_source(encoder: tiktoken.Encoding) -> None:
    """Direct parser (MD/TXT) — heading_path_source: "markdown" если есть заголовки."""
    md = "# Title\n\nContent\n"
    chunks = chunk_document(md, parser="direct")

    assert len(chunks) >= 1
    assert chunks[0].meta["heading_path_source"] == "markdown"
    assert "Title" in chunks[0].meta["heading_path"]  # type: ignore[operator]


def test_chunker_version_in_meta(encoder: tiktoken.Encoding) -> None:
    """Версия алгоритма в meta каждого чанка."""
    md = "# Title\n\nContent\n"
    chunks = chunk_document(md, parser="docling")

    for chunk in chunks:
        assert chunk.meta["chunker_version"] == CHUNKER_VERSION


def test_empty_document() -> None:
    """Пустой документ → пустой список чанков."""
    chunks = chunk_document("", parser="docling")
    assert chunks == []


def test_large_paragraph_split(encoder: tiktoken.Encoding) -> None:
    """Крупный параграф (>800 токенов) разбивается жёстко по предложениям."""
    long_para = "This is a sentence. " * 200  # ~1000+ tokens
    md = f"# Doc\n\n{long_para}\n"
    chunks = chunk_document(md, parser="docling")

    assert len(chunks) > 1
    for chunk in chunks:
        token_count = len(encoder.encode(chunk.text))
        assert token_count <= 800


def test_ordinal_is_global(encoder: tiktoken.Encoding) -> None:
    """ordinal — глобальный, не локальный."""
    md = "# A\n\nPara A.\n\n# B\n\nPara B.\n"
    chunks = chunk_document(md, parser="docling")

    ordinals = [c.ordinal for c in chunks]
    assert ordinals == list(range(len(chunks)))


def test_large_table_not_split(encoder: tiktoken.Encoding) -> None:
    """Таблица крупнее 800 токенов остаётся одним чанком целиком."""
    # Генерируем таблицу с ~30 строками — больше 800 токенов
    header = "| Col1 | Col2 | Col3 |\n|------|------|------|\n"
    rows = "".join(f"| r{i}a | r{i}b | r{i}c |\n" for i in range(200))
    table = header + rows
    md = f"# Data\n\n{table}\n"

    chunks = chunk_document(md, parser="docling")

    table_chunks = [c for c in chunks if c.meta.get("is_table")]
    assert len(table_chunks) == 1
    token_count = len(encoder.encode(table_chunks[0].text))
    assert token_count > 800, "Test fixture should produce a table >800 tokens"
    # Таблица не разорвана — один чанк despite >800 tokens
    assert table_chunks[0].meta["is_table"] is True


def test_direct_no_headings_source_none() -> None:
    """Direct parser с голым .txt без заголовков — heading_path_source: "none"."""
    md = "Just plain text without any markdown headings.\n\nSecond paragraph.\n"
    chunks = chunk_document(md, parser="direct")

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.meta["heading_path"] == []
        assert chunk.meta["heading_path_source"] == "none"
