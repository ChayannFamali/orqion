"""Тесты импорта наборов оценки (T-224).

Проверки:
- test_parse_codesearchnet: парсер читает JSONL, возвращает CodeSearchNetEntry
- test_parse_codesearchnet_skips_empty_lines: пустые строки пропускаются
- test_resolve_doc_ids_exact_filename: точное совпадение filename + content → [doc_id]
- test_resolve_doc_ids_basename_fallback: точного filename нет, basename + content → [doc_id]
- test_resolve_doc_ids_content_mismatch: filename совпал, content не совпал → []
- test_resolve_doc_ids_basename_collision: два документа с одинаковым basename,
  разным содержимым — выбирается правильный по content check
- test_resolve_doc_ids_no_candidate: нет кандидата по имени → []
- test_resolve_doc_ids_no_chunks: документ есть, но не проиндексирован (нет чанков) → []
- test_import_eval_set_basic: создание набора с элементами
- test_import_eval_set_empty_doc_ids: expected_doc_ids=[] сохраняется корректно
- test_import_codesearchnet_full: полный pipeline parse → resolve → import
- test_normalize_code: нормализация убирает пробелы и отступы
"""

from __future__ import annotations

from pathlib import Path

from app.db.models import Chunk, Corpus, Document, EvalItem, Workspace
from app.rag.eval_import import (
    CodeSearchNetEntry,
    ImportItem,
    _basename,
    _normalize_code,
    import_codesearchnet,
    import_eval_set,
    parse_codesearchnet,
    resolve_doc_ids,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


async def _make_workspace(session: AsyncSession) -> str:
    ws = Workspace(name="test-ws")
    session.add(ws)
    await session.flush()
    return ws.id


async def _make_corpus(session: AsyncSession, workspace_id: str) -> str:
    corpus = Corpus(workspace_id=workspace_id, name="test-corpus")
    session.add(corpus)
    await session.flush()
    return corpus.id


async def _make_document(
    session: AsyncSession,
    workspace_id: str,
    corpus_id: str,
    filename: str,
    sha256: str = "sha256-default",
) -> str:
    doc = Document(
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        blob_uri=sha256,
        filename=filename,
        mime="text/x-python",
        sha256=sha256,
        source_type="upload",
        status="indexed",
    )
    session.add(doc)
    await session.flush()
    return doc.id


async def _make_chunk(
    session: AsyncSession,
    workspace_id: str,
    index_version_id: str,
    document_id: str,
    text: str,
    ordinal: int = 0,
) -> str:
    chunk = Chunk(
        workspace_id=workspace_id,
        index_version_id=index_version_id,
        document_id=document_id,
        ordinal=ordinal,
        text=text,
        meta={},
    )
    session.add(chunk)
    await session.flush()
    return chunk.id


async def _make_index_version(session: AsyncSession, workspace_id: str, corpus_id: str) -> str:
    from app.db.models import IndexVersion

    iv = IndexVersion(
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        embedding_model="BAAI/bge-m3",
        chunker="code",
        chunker_version="1",
        status="active",
    )
    session.add(iv)
    await session.flush()
    return iv.id


# ---------------------------------------------------------------------------
# parse_codesearchnet
# ---------------------------------------------------------------------------


def test_parse_codesearchnet() -> None:
    """Парсер читает JSONL фикстуру и возвращает CodeSearchNetEntry."""
    entries = parse_codesearchnet(FIXTURES / "codesearchnet_sample.jsonl")
    assert len(entries) == 3
    assert entries[0].question == "How to parse JSON in Python?"
    assert "def parse_json" in entries[0].func_code
    assert entries[0].filepath == "python/utils.py"


def test_parse_codesearchnet_skips_empty_lines(tmp_path: Path) -> None:
    """Пустые строки в JSONL пропускаются."""
    p = tmp_path / "test.jsonl"
    p.write_text(
        '{"question": "Q1", "func_code": "def f(): pass", "filepath": "a.py"}\n'
        "\n"
        '{"question": "Q2", "func_code": "def g(): pass", "filepath": "b.py"}\n',
        encoding="utf-8",
    )
    entries = parse_codesearchnet(p)
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# _normalize_code
# ---------------------------------------------------------------------------


def test_normalize_code() -> None:
    """Нормализация убирает пробелы, отступы, переносы."""
    code1 = "def f():\n    return 1"
    code2 = "def f ( ) : return 1"
    assert _normalize_code(code1) == _normalize_code(code2)


# ---------------------------------------------------------------------------
# _basename
# ---------------------------------------------------------------------------


def test_basename() -> None:
    assert _basename("python/utils.py") == "utils.py"
    assert _basename("utils.py") == "utils.py"


# ---------------------------------------------------------------------------
# resolve_doc_ids
# ---------------------------------------------------------------------------


async def test_resolve_doc_ids_exact_filename(db_session: AsyncSession) -> None:
    """Точное совпадение filename + content match → [doc_id]."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    iv_id = await _make_index_version(db_session, ws_id, corpus_id)
    doc_id = await _make_document(db_session, ws_id, corpus_id, "python/utils.py")
    await _make_chunk(
        db_session, ws_id, iv_id, doc_id, "def parse_json(text):\n    return json.loads(text)"
    )

    entries = [
        CodeSearchNetEntry(
            "Q1", "def parse_json(text):\n    return json.loads(text)", "python/utils.py"
        )
    ]
    result = await resolve_doc_ids(db_session, corpus_id, entries)

    assert result["python/utils.py"] == [doc_id]


async def test_resolve_doc_ids_basename_fallback(db_session: AsyncSession) -> None:
    """Точного filename нет, basename + content → [doc_id]."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    iv_id = await _make_index_version(db_session, ws_id, corpus_id)
    doc_id = await _make_document(db_session, ws_id, corpus_id, "some/path/helpers.py")
    await _make_chunk(
        db_session,
        ws_id,
        iv_id,
        doc_id,
        "def read_lines(path):\n    with open(path) as f:\n        return f.readlines()",
    )

    entries = [
        CodeSearchNetEntry(
            "Q",
            "def read_lines(path):\n    with open(path) as f:\n        return f.readlines()",
            "python/helpers.py",
        )
    ]
    result = await resolve_doc_ids(db_session, corpus_id, entries)

    assert result["python/helpers.py"] == [doc_id]


async def test_resolve_doc_ids_content_mismatch(db_session: AsyncSession) -> None:
    """Filename совпал, content не совпал → [] (не ложное сопоставление)."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    iv_id = await _make_index_version(db_session, ws_id, corpus_id)
    doc_id = await _make_document(db_session, ws_id, corpus_id, "python/utils.py")
    await _make_chunk(db_session, ws_id, iv_id, doc_id, "def different_function():\n    pass")

    entries = [
        CodeSearchNetEntry(
            "Q", "def parse_json(text):\n    return json.loads(text)", "python/utils.py"
        )
    ]
    result = await resolve_doc_ids(db_session, corpus_id, entries)

    assert result["python/utils.py"] == []


async def test_resolve_doc_ids_basename_collision(db_session: AsyncSession) -> None:
    """Два документа с одинаковым basename, разным содержимым —
    выбирается правильный по content check, не первый попавшийся."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    iv_id = await _make_index_version(db_session, ws_id, corpus_id)

    doc_a = await _make_document(db_session, ws_id, corpus_id, "repo_a/utils.py", sha256="aaa")
    doc_b = await _make_document(db_session, ws_id, corpus_id, "repo_b/utils.py", sha256="bbb")

    await _make_chunk(
        db_session,
        ws_id,
        iv_id,
        doc_a,
        "def sort_by_key(items, key):\n    return sorted(items, key=lambda x: x[key])",
    )
    await _make_chunk(
        db_session, ws_id, iv_id, doc_b, "def parse_json(text):\n    return json.loads(text)"
    )

    # Entry с func_code из doc_a — должен сопоставиться с doc_a, не doc_b
    entries = [
        CodeSearchNetEntry(
            "Q",
            "def sort_by_key(items, key):\n    return sorted(items, key=lambda x: x[key])",
            "other/utils.py",
        )
    ]
    result = await resolve_doc_ids(db_session, corpus_id, entries)

    assert result["other/utils.py"] == [doc_a]
    assert doc_b not in result["other/utils.py"]


async def test_resolve_doc_ids_no_candidate(db_session: AsyncSession) -> None:
    """Нет кандидата по имени → []."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    # Нет документов в корпусе

    entries = [CodeSearchNetEntry("Q", "def f(): pass", "nonexistent.py")]
    result = await resolve_doc_ids(db_session, corpus_id, entries)

    assert result["nonexistent.py"] == []


async def test_resolve_doc_ids_no_chunks(db_session: AsyncSession) -> None:
    """Документ есть, но не проиндексирован (нет чанков) → []."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    await _make_document(db_session, ws_id, corpus_id, "python/utils.py")
    # Нет IndexVersion, нет Chunk

    entries = [CodeSearchNetEntry("Q", "def f(): pass", "python/utils.py")]
    result = await resolve_doc_ids(db_session, corpus_id, entries)

    assert result["python/utils.py"] == []


# ---------------------------------------------------------------------------
# import_eval_set
# ---------------------------------------------------------------------------


async def test_import_eval_set_basic(db_session: AsyncSession) -> None:
    """Создание набора с элементами."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)

    items = [
        ImportItem(question="Q1", expected_doc_ids=["doc-1"], expected_answer="def f(): pass"),
        ImportItem(question="Q2", expected_doc_ids=[], expected_answer=None),
    ]
    eval_set = await import_eval_set(db_session, ws_id, corpus_id, "test-set", items)
    await db_session.flush()

    assert eval_set.id is not None
    assert eval_set.name == "test-set"
    assert eval_set.corpus_id == corpus_id

    # Явный запрос items (lazy loading не работает в async)
    item_result = await db_session.execute(
        select(EvalItem).where(EvalItem.eval_set_id == eval_set.id)
    )
    db_items = list(item_result.scalars().all())
    assert len(db_items) == 2
    assert db_items[0].question == "Q1"
    assert db_items[0].expected_doc_ids == ["doc-1"]
    assert db_items[1].expected_doc_ids == []
    assert db_items[1].expected_answer is None


async def test_import_eval_set_empty_doc_ids(db_session: AsyncSession) -> None:
    """expected_doc_ids=[] сохраняется корректно (эталона нет)."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)

    items = [ImportItem(question="Q?", expected_doc_ids=[], expected_answer=None)]
    eval_set = await import_eval_set(db_session, ws_id, corpus_id, "empty-set", items)
    await db_session.flush()

    item_result = await db_session.execute(
        select(EvalItem).where(EvalItem.eval_set_id == eval_set.id)
    )
    db_items = list(item_result.scalars().all())
    assert db_items[0].expected_doc_ids == []


# ---------------------------------------------------------------------------
# import_codesearchnet (full pipeline)
# ---------------------------------------------------------------------------


async def test_import_codesearchnet_full(db_session: AsyncSession) -> None:
    """Полный pipeline: parse → resolve → import."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    iv_id = await _make_index_version(db_session, ws_id, corpus_id)

    # Создаём документы и чанки для двух из трёх entries
    doc1 = await _make_document(db_session, ws_id, corpus_id, "python/utils.py", sha256="sha1")
    doc2 = await _make_document(db_session, ws_id, corpus_id, "python/helpers.py", sha256="sha2")
    await _make_chunk(
        db_session, ws_id, iv_id, doc1, "def parse_json(text):\n    return json.loads(text)"
    )
    await _make_chunk(
        db_session,
        ws_id,
        iv_id,
        doc2,
        "def read_lines(path):\n    with open(path) as f:\n        return f.readlines()",
    )
    # other/utils.py — нет документа, expected_doc_ids будет []

    eval_set, matched = await import_codesearchnet(
        db_session, ws_id, corpus_id, "csn-test", FIXTURES / "codesearchnet_sample.jsonl"
    )
    await db_session.flush()

    assert eval_set.id is not None
    assert matched == 2  # 2 из 3 нашли документ

    # Явный запрос items (lazy loading не работает в async)
    item_result = await db_session.execute(
        select(EvalItem).where(EvalItem.eval_set_id == eval_set.id).order_by(EvalItem.question)
    )
    db_items = list(item_result.scalars().all())
    assert len(db_items) == 3

    # Первый — с документом (python/utils.py)
    assert db_items[0].expected_doc_ids == [doc1]
    # Второй — с документом (python/helpers.py)
    assert db_items[1].expected_doc_ids == [doc2]
    # Третий — без документа (other/utils.py не найден)
    assert db_items[2].expected_doc_ids == []
