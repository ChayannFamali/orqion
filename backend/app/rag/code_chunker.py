"""Чанкинг кода по символам через tree-sitter (T-209, S-23).

Разбивает исходный код на чанки по границам функций и классов (ADR-9).
Единица чанка — функция или класс целиком. Крупные символы дробятся,
но сигнатура и путь к файлу дублируются в каждом фрагменте (arch.md §8.1).

Файл без поддерживаемой грамматики — резервный текстовый чанкинг с пометкой.

Поддерживаемые языки: Python, C++, TypeScript, TSX, Go, Java.
SQL — отдельная задача T-210.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tiktoken
from tree_sitter import Node

from app.rag.treesitter import detect_language, parse_code

CODE_CHUNKER_VERSION = "1.0"

MAX_TOKENS = 800


@dataclass(frozen=True)
class CodeChunk:
    """Чанк кода — функция, класс или фрагмент крупного символа."""

    text: str
    file_path: str
    language: str | None
    symbol: str | None
    parent: str | None
    signature: str | None
    imports: list[str]
    start_line: int
    end_line: int
    meta: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Конфигурация по языкам
# ---------------------------------------------------------------------------

# Атомарные символы — функции, методы. Всегда становятся отдельными чанками.
# parent определяется через _find_parent_name (обход вверх до контейнера).
_ATOMIC_TYPES: dict[str, set[str]] = {
    "python": {"function_definition"},
    "cpp": {"function_definition"},
    "typescript": {"function_declaration", "method_definition"},
    "tsx": {"function_declaration", "method_definition"},
    "go": {"function_declaration", "method_declaration"},
    "java": {"method_declaration"},
}

# Контейнеры — классы, структуры, интерфейсы.
# Если внутри есть методы — каждый метод становится отдельным чанком,
# parent = имя контейнера. Если методов нет (dataclass, enum, struct
# с одними полями) — контейнер целиком один чанк (ADR-9).
_CONTAINER_TYPES: dict[str, set[str]] = {
    "python": {"class_definition"},
    "cpp": {"class_specifier"},
    "typescript": {"class_declaration"},
    "tsx": {"class_declaration"},
    "go": {"type_declaration"},
    "java": {"class_declaration", "interface_declaration"},
}

# Типы узлов для импортов
_IMPORT_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "cpp": {"preproc_include"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
    "go": {"import_declaration"},
    "java": {"import_declaration"},
}


# ---------------------------------------------------------------------------
# Извлечение имени символа
# ---------------------------------------------------------------------------


def _extract_symbol_name(node: Node, language: str) -> str | None:
    """Извлекает имя символа из узла AST.

    Разные грамматики хранят имя по-разному:
    - Python, TypeScript, Go, Java: field 'name'
    - C++: function_declarator → field_identifier/identifier
    - Go type_declaration: type_spec → field 'name'
    """
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        text = name_node.text
        if text is not None:
            return text.decode()

    # C++: function_definition → declarator → function_declarator → identifier
    if language == "cpp" and node.type == "function_definition":
        decl = node.child_by_field_name("declarator")
        if decl is not None and decl.type == "function_declarator":
            for child in decl.children:
                if child.type in ("identifier", "field_identifier"):
                    text = child.text
                    if text is not None:
                        return text.decode()

    # Go: type_declaration → type_spec → name
    if language == "go" and node.type == "type_declaration":
        for child in node.children:
            if child.type == "type_spec":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    text = name_node.text
                    if text is not None:
                        return text.decode()

    return None


def _find_parent_name(node: Node, language: str) -> str | None:
    """Поднимается по дереву до ближайшего контейнера (класс/структура/интерфейс).

    Для Go method_declaration — top-level узел с receiver, не child of
    type_declaration в AST. Тип извлекается из поля receiver (T-209a):
    parameter_list → parameter_declaration → type → type_identifier,
    с отбрасыванием pointer_type (*Server → Server).
    """
    if language == "go" and node.type == "method_declaration":
        return _extract_go_receiver_type(node)

    container_types = _CONTAINER_TYPES.get(language, set())
    current = node.parent
    while current is not None:
        if current.type in container_types:
            name = _extract_symbol_name(current, language)
            if name is not None:
                return name
        current = current.parent
    return None


def _extract_go_receiver_type(node: Node) -> str | None:
    """Извлекает имя типа из receiver Go method_declaration.

    receiver → parameter_list → parameter_declaration → type
    type может быть type_identifier (Server) или pointer_type (*Server → Server).
    """
    recv = node.child_by_field_name("receiver")
    if recv is None or recv.type != "parameter_list":
        return None

    for child in recv.children:
        if child.type != "parameter_declaration":
            continue
        type_node = child.child_by_field_name("type")
        if type_node is None:
            continue
        if type_node.type == "type_identifier":
            text = type_node.text
            return text.decode() if text is not None else None
        if type_node.type == "pointer_type":
            # pointer_type → type_identifier
            for sub in type_node.children:
                if sub.type == "type_identifier":
                    text = sub.text
                    return text.decode() if text is not None else None

    return None


# ---------------------------------------------------------------------------
# Извлечение сигнатуры
# ---------------------------------------------------------------------------


def _extract_signature(node: Node, source: bytes) -> str:
    """Извлекает сигнатуру — первую строку объявления символа.

    Для функции: 'def foo(x: int) -> str:'
    Для класса: 'class Foo(Bar):'
    Для Go method: 'func (b Bar) method()'
    """
    lines = source.decode("utf-8", errors="replace").split("\n")
    start_line = node.start_point[0]
    if start_line >= len(lines):
        return ""
    return lines[start_line].strip()


# ---------------------------------------------------------------------------
# Извлечение импортов
# ---------------------------------------------------------------------------


def _extract_imports(root: Node, language: str) -> list[str]:
    """Собирает import-узлы как список строк."""
    import_types = _IMPORT_TYPES.get(language, set())
    if not import_types:
        return []

    imports: list[str] = []
    for child in root.children:
        if child.type in import_types:
            text = child.text
            if text is not None:
                imports.append(text.decode().strip())
    return imports


# ---------------------------------------------------------------------------
# Обход AST и сбор символов
# ---------------------------------------------------------------------------


def _collect_symbols(
    node: Node,
    language: str,
    source: bytes,
    file_path: str,
    imports: list[str],
    encoder: tiktoken.Encoding,
) -> list[CodeChunk]:
    """Рекурсивно обходит AST и собирает символы.

    Контейнер (класс/тип) с методами → каждый метод отдельный чанк,
    parent = имя контейнера. Контейнер без методов → один чанк целиком (ADR-9).
    Атомарный символ (функция/метод) → один чанк или дробление если > MAX_TOKENS.
    """
    container_types = _CONTAINER_TYPES.get(language, set())
    atomic_types = _ATOMIC_TYPES.get(language, set())
    chunks: list[CodeChunk] = []

    if node.type in container_types:
        # Контейнер: ищем методы внутри
        method_chunks: list[CodeChunk] = []
        for child in node.children:
            method_chunks.extend(
                _collect_symbols(child, language, source, file_path, imports, encoder)
            )

        if method_chunks:
            # Методы найдены — каждый метод отдельный чанк
            chunks.extend(method_chunks)
        else:
            # Нет методов — контейнер атомарный (dataclass, enum, struct с полями)
            _append_symbol_chunks(node, language, source, file_path, imports, encoder, chunks)
        return chunks

    if node.type in atomic_types:
        # Атомарный символ (функция/метод)
        _append_symbol_chunks(node, language, source, file_path, imports, encoder, chunks)
        return chunks

    # Рекурсивный обход дочерних узлов
    for child in node.children:
        chunks.extend(_collect_symbols(child, language, source, file_path, imports, encoder))
    return chunks


def _append_symbol_chunks(
    node: Node,
    language: str,
    source: bytes,
    file_path: str,
    imports: list[str],
    encoder: tiktoken.Encoding,
    chunks: list[CodeChunk],
) -> None:
    """Создаёт чанк(и) для атомарного символа или контейнера без методов."""
    symbol_name = _extract_symbol_name(node, language) or "<anonymous>"
    parent_name = _find_parent_name(node, language)
    signature = _extract_signature(node, source)

    node_text = node.text
    if node_text is None:
        return
    text = node_text.decode("utf-8", errors="replace")
    token_count = len(encoder.encode(text))

    if token_count <= MAX_TOKENS:
        chunks.append(
            _make_chunk(
                text=text,
                file_path=file_path,
                language=language,
                symbol=symbol_name,
                parent=parent_name,
                signature=signature,
                imports=imports,
                start_line=node.start_point[0],
                end_line=node.end_point[0],
                encoder=encoder,
            )
        )
    else:
        fragments = _split_large_symbol(node, source, encoder, symbol_name, signature, file_path)
        for frag_text, frag_start, frag_end in fragments:
            chunks.append(
                _make_chunk(
                    text=frag_text,
                    file_path=file_path,
                    language=language,
                    symbol=symbol_name,
                    parent=parent_name,
                    signature=signature,
                    imports=imports,
                    start_line=frag_start,
                    end_line=frag_end,
                    encoder=encoder,
                )
            )


def _make_chunk(
    text: str,
    file_path: str,
    language: str,
    symbol: str,
    parent: str | None,
    signature: str,
    imports: list[str],
    start_line: int,
    end_line: int,
    encoder: tiktoken.Encoding,
) -> CodeChunk:
    """Создаёт CodeChunk с метаданными."""
    return CodeChunk(
        text=text,
        file_path=file_path,
        language=language,
        symbol=symbol,
        parent=parent,
        signature=signature,
        imports=imports,
        start_line=start_line,
        end_line=end_line,
        meta={
            "code_chunker_version": CODE_CHUNKER_VERSION,
            "token_count": len(encoder.encode(text)),
        },
    )


# ---------------------------------------------------------------------------
# Дробление крупных символов
# ---------------------------------------------------------------------------


def _split_large_symbol(
    node: Node,
    source: bytes,
    encoder: tiktoken.Encoding,
    symbol_name: str,
    signature: str,
    file_path: str,
) -> list[tuple[str, int, int]]:
    """Дробит крупный символ на фрагменты по строкам.

    Каждый фрагмент начинается с дублированной сигнатуры (arch.md §8.1).
    file_path также дублируется в метаданных каждого фрагмента.
    Возвращает список (text, start_line, end_line).
    """
    lines = source.decode("utf-8", errors="replace").split("\n")
    start_line = node.start_point[0]
    end_line = node.end_point[0]

    # Тело символа без первой строки (сигнатура)
    body_lines = lines[start_line + 1 : end_line + 1]

    fragments: list[tuple[str, int, int]] = []
    current_lines: list[str] = []

    for i, line in enumerate(body_lines):
        current_lines.append(line)
        frag_text = signature + "\n" + "\n".join(current_lines)
        token_count = len(encoder.encode(frag_text))

        if token_count > MAX_TOKENS and len(current_lines) > 1:
            # Убираем последнюю строку — она превысила лимит
            current_lines.pop()
            frag_text = signature + "\n" + "\n".join(current_lines)
            frag_start = start_line + 1 + i - len(current_lines) if fragments else start_line + 1
            frag_end = start_line + i
            fragments.append((frag_text, frag_start, frag_end))
            current_lines = [line]

    if current_lines:
        frag_text = signature + "\n" + "\n".join(current_lines)
        frag_start = (
            start_line + 1 + len(body_lines) - len(current_lines) if fragments else start_line
        )
        frag_end = end_line
        fragments.append((frag_text, frag_start, frag_end))

    return fragments


# ---------------------------------------------------------------------------
# Fallback: файл без грамматики
# ---------------------------------------------------------------------------


def _fallback_chunk(
    source: bytes,
    file_path: str,
    encoder: tiktoken.Encoding,
) -> list[CodeChunk]:
    """Резервный текстовый чанкинг для файлов без поддерживаемой грамматики.

    Окна по 500 токенов, без перекрытия, без иллюзии структуры.
    Аналог fallback-PDF в T-206 — пометка, что это неструктурированный текст.
    """
    text = source.decode("utf-8", errors="replace")
    lines = text.split("\n")

    chunks: list[CodeChunk] = []
    current_lines: list[str] = []
    chunk_start = 0

    for i, line in enumerate(lines):
        current_lines.append(line)
        frag = "\n".join(current_lines)
        token_count = len(encoder.encode(frag))

        if token_count > 500 and len(current_lines) > 1:
            current_lines.pop()
            frag = "\n".join(current_lines)
            chunks.append(
                CodeChunk(
                    text=frag,
                    file_path=file_path,
                    language=None,
                    symbol=None,
                    parent=None,
                    signature=None,
                    imports=[],
                    start_line=chunk_start,
                    end_line=i - 1,
                    meta={
                        "code_chunker_version": CODE_CHUNKER_VERSION,
                        "fallback": True,
                        "token_count": len(encoder.encode(frag)),
                    },
                )
            )
            current_lines = [line]
            chunk_start = i
        elif token_count > 500 and len(current_lines) == 1:
            # Одна строка превышает лимит — дробим по токенам
            tokens = encoder.encode(line)
            for j in range(0, len(tokens), 500):
                frag_tokens = tokens[j : j + 500]
                frag = encoder.decode(frag_tokens)
                chunks.append(
                    CodeChunk(
                        text=frag,
                        file_path=file_path,
                        language=None,
                        symbol=None,
                        parent=None,
                        signature=None,
                        imports=[],
                        start_line=i,
                        end_line=i,
                        meta={
                            "code_chunker_version": CODE_CHUNKER_VERSION,
                            "fallback": True,
                            "token_count": len(frag_tokens),
                        },
                    ),
                )
            current_lines = []
            chunk_start = i + 1

    if current_lines:
        frag = "\n".join(current_lines)
        chunks.append(
            CodeChunk(
                text=frag,
                file_path=file_path,
                language=None,
                symbol=None,
                parent=None,
                signature=None,
                imports=[],
                start_line=chunk_start,
                end_line=len(lines) - 1 if lines else 0,
                meta={
                    "code_chunker_version": CODE_CHUNKER_VERSION,
                    "fallback": True,
                    "token_count": len(encoder.encode(frag)),
                },
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def chunk_code(
    source: bytes,
    file_path: str | Path,
) -> list[CodeChunk]:
    """Разбивает исходный код на чанки по символам.

    Args:
        source: исходный код в байтах.
        file_path: путь к файлу (для определения языка и метаданных).

    Returns:
        Список чанков кода с метаданными.
    """
    file_path_str = str(file_path)
    language = detect_language(file_path_str)
    encoder = tiktoken.encoding_for_model("gpt-4")

    if language is None:
        return _fallback_chunk(source, file_path_str, encoder)

    tree = parse_code(source, language)
    root = tree.root_node
    imports = _extract_imports(root, language)

    return _collect_symbols(root, language, source, file_path_str, imports, encoder)
