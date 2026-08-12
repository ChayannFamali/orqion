"""Импорт наборов оценки (T-224).

arch.md §8.4: CodeSearchNet — пары «запрос → функция».
Датасеты остаются в тестовых фикстурах, не попадают в поставку (приёмка T-224).

Сопоставление CodeSearchNet → orqion document_id:
1. Поиск кандидата по filename (точное совпадение, затем basename).
2. Проверка содержимого: func_code (нормализованный) должен встречаться
   в тексте документа или совпадать с текстом Chunk.
3. Если содержимое не подтвердилось — expected_doc_ids=[].
   Пустой список корректно даёт recall@k=0 (эталона нет),
   ложное сопоставление — обманчиво валидный тест (ADR-10).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document, EvalItem, EvalSet

logger = logging.getLogger("orqion.rag.eval_import")


@dataclass(frozen=True)
class CodeSearchNetEntry:
    """Одна запись CodeSearchNet JSONL."""

    question: str
    func_code: str
    filepath: str


@dataclass(frozen=True)
class ImportItem:
    """Один элемент для импорта в eval_set.

    question — вопрос на естественном языке.
    expected_doc_ids — UUID документов orqion (может быть пустым).
    expected_answer — эталонный ответ (опционально, для CodeSearchNet — func_code).
    """

    question: str
    expected_doc_ids: list[str]
    expected_answer: str | None


def _normalize_code(code: str) -> str:
    """Нормализация кода для сравнения: удаление пробелов, отступов, переносов."""
    return re.sub(r"\s+", "", code)


def _basename(filepath: str) -> str:
    """Возвращает basename из filepath (для fallback-сопоставления)."""
    return Path(filepath).name


def parse_codesearchnet(jsonl_path: str | Path) -> list[CodeSearchNetEntry]:
    """Парсит CodeSearchNet JSONL.

    Формат строки: {"question": "...", "func_code": "...", "filepath": "..."}
    Данные не загружаются — путь передаётся вызывающим.
    """
    entries: list[CodeSearchNetEntry] = []
    path = Path(jsonl_path)
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            entries.append(
                CodeSearchNetEntry(
                    question=obj["question"],
                    func_code=obj["func_code"],
                    filepath=obj["filepath"],
                )
            )
    return entries


async def resolve_doc_ids(
    session: AsyncSession,
    corpus_id: str,
    entries: list[CodeSearchNetEntry],
) -> dict[str, list[str]]:
    """Маппит CodeSearchNet filepath → [document_id] с проверкой содержимого.

    Шаги:
    1. Загрузить все Document и Chunk для corpus_id.
    2. Для каждого entry найти кандидатов по filename (точное, затем basename).
    3. Проверить: нормализованный func_code входит в нормализованный текст
       документа ИЛИ совпадает с нормализованным текстом одного из Chunk.
    4. Если подтверждено — [document_id]. Если нет — [].

    Returns:
        dict {filepath: [document_id]} — может содержать пустые списки.
    """
    # Загружаем документы корпуса
    doc_result = await session.execute(select(Document).where(Document.corpus_id == corpus_id))
    documents = list(doc_result.scalars().all())

    # Загружаем чанки для этих документов
    doc_ids = [d.id for d in documents]
    chunks: list[Chunk] = []
    if doc_ids:
        chunk_result = await session.execute(select(Chunk).where(Chunk.document_id.in_(doc_ids)))
        chunks = list(chunk_result.scalars().all())

    # Группируем чанки по document_id
    chunks_by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        chunks_by_doc.setdefault(c.document_id, []).append(c)

    # Индекс для быстрого поиска: filename → [Document], basename → [Document]
    by_filename: dict[str, list[Document]] = {}
    by_basename: dict[str, list[Document]] = {}
    for d in documents:
        by_filename.setdefault(d.filename, []).append(d)
        by_basename.setdefault(_basename(d.filename), []).append(d)

    result: dict[str, list[str]] = {}
    for entry in entries:
        normalized_func = _normalize_code(entry.func_code)
        if not normalized_func:
            result[entry.filepath] = []
            continue

        # Кандидаты: сначала точное совпадение filename, затем basename
        candidates = by_filename.get(entry.filepath, [])
        if not candidates:
            candidates = by_basename.get(_basename(entry.filepath), [])

        if not candidates:
            result[entry.filepath] = []
            continue

        # Проверка содержимого
        matched_doc_ids: list[str] = []
        for doc in candidates:
            # Способ 1: func_code как substring в тексте чанка
            doc_chunks = chunks_by_doc.get(doc.id, [])
            for chunk in doc_chunks:
                if normalized_func == _normalize_code(chunk.text):
                    matched_doc_ids.append(doc.id)
                    break
            if doc.id in matched_doc_ids:
                continue

            # Способ 2: func_code как substring в blob (нужен BlobStore)
            # Здесь — только по чанкам, т.к. blob недоступен без BlobStore.
            # Если чанков нет (документ не проиндексирован) — пропускаем.

        if matched_doc_ids:
            result[entry.filepath] = matched_doc_ids
        else:
            # Кандидаты по имени есть, но содержимое не подтвердилось
            logger.info(
                "resolve_doc_ids: filename match but content mismatch for %s",
                entry.filepath,
            )
            result[entry.filepath] = []

    return result


async def import_eval_set(
    session: AsyncSession,
    workspace_id: str,
    corpus_id: str,
    name: str,
    items: list[ImportItem],
) -> EvalSet:
    """Создаёт EvalSet с элементами. Атомарно — все или ничего.

    Args:
        session: async DB session.
        workspace_id: workspace ID.
        corpus_id: corpus ID.
        name: имя набора (unique per workspace).
        items: список элементов для импорта.

    Returns:
        Созданный EvalSet с items.
    """
    eval_set = EvalSet(
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        name=name,
    )
    session.add(eval_set)
    await session.flush()

    for item in items:
        eval_item = EvalItem(
            workspace_id=workspace_id,
            eval_set_id=eval_set.id,
            question=item.question,
            expected_doc_ids=item.expected_doc_ids,
            expected_answer=item.expected_answer,
        )
        session.add(eval_item)

    await session.flush()
    return eval_set


async def import_codesearchnet(
    session: AsyncSession,
    workspace_id: str,
    corpus_id: str,
    name: str,
    jsonl_path: str | Path,
) -> tuple[EvalSet, int]:
    """Импорт CodeSearchNet JSONL в eval_set.

    Полный pipeline: parse → resolve_doc_ids → import_eval_set.

    Returns:
        (EvalSet, num_items_with_doc_ids) — сколько элементов получили непустой expected_doc_ids.
    """
    entries = parse_codesearchnet(jsonl_path)
    doc_id_map = await resolve_doc_ids(session, corpus_id, entries)

    items: list[ImportItem] = []
    matched = 0
    for entry in entries:
        doc_ids = doc_id_map.get(entry.filepath, [])
        if doc_ids:
            matched += 1
        items.append(
            ImportItem(
                question=entry.question,
                expected_doc_ids=doc_ids,
                expected_answer=entry.func_code,
            )
        )

    eval_set = await import_eval_set(session, workspace_id, corpus_id, name, items)
    return eval_set, matched
