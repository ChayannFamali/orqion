"""Инфраструктура разбора кода через tree-sitter (T-208, S-23).

Выбор грамматики по расширению файла, ленивое создание парсеров с кэшированием.
Парсинг исходного кода в AST Tree — без чанкинга (T-209).

Поддерживаемые языки: Python, C++, TypeScript, TSX, Go, Java, SQL.
Файл без поддерживаемой грамматики — detect_language возвращает None,
T-209 обрабатывает резервным текстовым чанкингом с пометкой (ADR-9).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from tree_sitter import Language, Parser, Tree

# ---------------------------------------------------------------------------
# Сопоставление расширений и языков
# ---------------------------------------------------------------------------

_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".h": "cpp",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".sql": "sql",
}

SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {"python", "cpp", "typescript", "tsx", "go", "java", "sql"}
)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_EXTENSION_TO_LANGUAGE.keys())


def detect_language(file_path: str | Path) -> str | None:
    """Определяет язык программирования по расширению файла.

    Возвращает имя языка ("python", "cpp", ...) или None,
    если расширение не поддерживается tree-sitter.
    """
    ext = Path(file_path).suffix.lower()
    return _EXTENSION_TO_LANGUAGE.get(ext)


# ---------------------------------------------------------------------------
# Ленивая загрузка грамматик
# ---------------------------------------------------------------------------

# Грамматические модули имеют разный API:
# - большинство: module.language()
# - tree_sitter_typescript: module.language_typescript() + module.language_tsx()
# Возвращают capsule-объект TSLanguage, который оборачивается в Language.


def _load_python() -> Any:
    import tree_sitter_python

    return tree_sitter_python.language()


def _load_cpp() -> Any:
    import tree_sitter_cpp

    return tree_sitter_cpp.language()


def _load_typescript() -> Any:
    import tree_sitter_typescript

    return tree_sitter_typescript.language_typescript()


def _load_tsx() -> Any:
    import tree_sitter_typescript

    return tree_sitter_typescript.language_tsx()


def _load_go() -> Any:
    import tree_sitter_go

    return tree_sitter_go.language()


def _load_java() -> Any:
    import tree_sitter_java

    return tree_sitter_java.language()


def _load_sql() -> Any:
    import tree_sitter_sql

    return tree_sitter_sql.language()


_LANGUAGE_LOADERS: dict[str, Callable[[], Any]] = {
    "python": _load_python,
    "cpp": _load_cpp,
    "typescript": _load_typescript,
    "tsx": _load_tsx,
    "go": _load_go,
    "java": _load_java,
    "sql": _load_sql,
}

# Кэш парсеров: language → Parser
_parsers: dict[str, Parser] = {}


def get_parser(language: str) -> Parser:
    """Возвращает кэшированный Parser для языка.

    Raises:
        ValueError: язык не поддерживается.
    """
    if language in _parsers:
        return _parsers[language]

    loader = _LANGUAGE_LOADERS.get(language)
    if loader is None:
        raise ValueError(f"Неподдерживаемый язык: {language}")

    lang = Language(loader())
    parser = Parser(lang)
    _parsers[language] = parser
    return parser


def parse_code(source: bytes, language: str) -> Tree:
    """Парсит исходный код в AST Tree.

    Args:
        source: исходный код в байтах.
        language: имя языка из detect_language.

    Returns:
        Tree — корень AST.

    Raises:
        ValueError: язык не поддерживается.
    """
    parser = get_parser(language)
    return parser.parse(source)
