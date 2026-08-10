"""Чанкинг SQL по statement (T-210, S-23).

Разбивает SQL-файл на чанки по границам statement. Метаданные каждого чанка:
тип операции (SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, CTE)
и имена затронутых таблиц.

Единица чанка — statement целиком (arch.md §8.1).
SQL — отдельный конвейер от code_chunker (ADR-9: «SQL требует разбиения
по statement с извлечением имён таблиц, а не по функциям»).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node

from app.rag.treesitter import detect_language, parse_code

SQL_CHUNKER_VERSION = "1.0"

# Маппинг node type → человекочитаемый тип операции
_OPERATION_MAP: dict[str, str] = {
    "select": "SELECT",
    "insert": "INSERT",
    "update": "UPDATE",
    "delete": "DELETE",
    "create_table": "CREATE",
    "alter_table": "ALTER",
    "drop_table": "DROP",
    "keyword_with": "CTE",
}


@dataclass(frozen=True)
class SqlChunk:
    """Чанк SQL — один statement."""

    text: str
    file_path: str
    operation: str
    tables: list[str]
    start_line: int
    end_line: int
    meta: dict[str, object] = field(default_factory=dict)


def _extract_operation(stmt: Node) -> str:
    """Определяет тип операции по первому значимому дочернему узлу statement."""
    for child in stmt.children:
        if child.type == ";":
            continue
        op = _OPERATION_MAP.get(child.type)
        if op is not None:
            return op
        # Неизвестный тип — возвращаем как есть
        return child.type.upper()
    return "UNKNOWN"


def _extract_tables(stmt: Node) -> list[str]:
    """Извлекает имена таблиц из statement — поиск object_reference узлов.

    object_reference содержит identifier с именем таблицы.
    Встречается в: FROM clause (relation), JOIN clause (relation),
    INSERT INTO, UPDATE relation, CREATE TABLE, ALTER TABLE, DROP TABLE.
    """
    tables: list[str] = []

    def _walk(node: Node) -> None:
        if node.type == "object_reference":
            # object_reference → identifier (имя таблицы)
            for child in node.children:
                if child.type == "identifier":
                    text = child.text
                    if text is not None:
                        name = text.decode()
                        if name not in tables:
                            tables.append(name)
                    return
        for child in node.children:
            _walk(child)

    _walk(stmt)
    return tables


def chunk_sql(
    source: bytes,
    file_path: str | Path,
) -> list[SqlChunk]:
    """Разбивает SQL-файл на чанки по statement.

    Args:
        source: исходный код в байтах.
        file_path: путь к файлу (для метаданных).

    Returns:
        Список чанков SQL с типом операции и именами таблиц.
    """
    file_path_str = str(file_path)
    language = detect_language(file_path_str)

    if language != "sql":
        # Не SQL-файл — пустой список, код-чанкер обрабатывает через code_chunker
        return []

    tree = parse_code(source, language)
    root = tree.root_node

    chunks: list[SqlChunk] = []
    ordinal = 0

    for child in root.children:
        if child.type != "statement":
            continue

        node_text = child.text
        if node_text is None:
            continue
        text = node_text.decode("utf-8", errors="replace").strip()

        if not text:
            continue

        operation = _extract_operation(child)
        tables = _extract_tables(child)

        chunks.append(
            SqlChunk(
                text=text,
                file_path=file_path_str,
                operation=operation,
                tables=tables,
                start_line=child.start_point[0],
                end_line=child.end_point[0],
                meta={
                    "sql_chunker_version": SQL_CHUNKER_VERSION,
                    "ordinal": ordinal,
                },
            )
        )
        ordinal += 1

    return chunks
