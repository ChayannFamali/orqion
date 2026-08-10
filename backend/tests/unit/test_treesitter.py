"""Тесты tree-sitter инфраструктуры (T-208, S-23).

Проверки:
- detect_language для всех поддерживаемых расширений
- detect_language возвращает None для неподдерживаемого расширения
- parse_code для каждого языка — корректный root node и ожидаемые дочерние узлы
- TypeScript vs TSX — разные грамматики по расширению
- Парсер кэшируется (один экземпляр на язык)
- get_parser raises ValueError для неподдерживаемого языка
- SUPPORTED_LANGUAGES и SUPPORTED_EXTENSIONS согласованы
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from app.rag.treesitter import (
    _EXTENSION_TO_LANGUAGE,
    _LANGUAGE_LOADERS,
    SUPPORTED_LANGUAGES,
    _parsers,
    detect_language,
    get_parser,
    parse_code,
)
from tree_sitter import Tree

# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ext", "expected"),
    [
        (".py", "python"),
        (".cpp", "cpp"),
        (".cc", "cpp"),
        (".cxx", "cpp"),
        (".hpp", "cpp"),
        (".hxx", "cpp"),
        (".h", "cpp"),
        (".ts", "typescript"),
        (".tsx", "tsx"),
        (".go", "go"),
        (".java", "java"),
        (".sql", "sql"),
    ],
)
def test_detect_language_known_extensions(ext: str, expected: str) -> None:
    """Каждое поддерживаемое расширение сопоставляется с правильным языком."""
    assert detect_language(f"file{ext}") == expected


def test_detect_language_uppercase_extension() -> None:
    """Расширение в верхнем регистре распознаётся."""
    assert detect_language("main.PY") == "python"
    assert detect_language("app.TSX") == "tsx"


def test_detect_language_unsupported_extension() -> None:
    """Неподдерживаемое расширение → None."""
    assert detect_language("file.rb") is None
    assert detect_language("file.rs") is None
    assert detect_language("file.md") is None
    assert detect_language("file.txt") is None


def test_detect_language_no_extension() -> None:
    """Файл без расширения → None."""
    assert detect_language("Makefile") is None
    assert detect_language("Dockerfile") is None


# ---------------------------------------------------------------------------
# parse_code — smoke-тесты для каждого языка
# ---------------------------------------------------------------------------


def test_parse_python() -> None:
    """Python: function_definition как дочерний узел root."""
    code = b"def foo():\n    return 42\n"
    tree = parse_code(code, "python")
    assert isinstance(tree, Tree)
    assert tree.root_node.type == "module"
    child_types = [c.type for c in tree.root_node.children]
    assert "function_definition" in child_types


def test_parse_cpp() -> None:
    """C++: function_definition как дочерний узел translation_unit."""
    code = b"int main() {\n    return 0;\n}\n"
    tree = parse_code(code, "cpp")
    assert isinstance(tree, Tree)
    assert tree.root_node.type == "translation_unit"
    child_types = [c.type for c in tree.root_node.children]
    assert "function_definition" in child_types


def test_parse_typescript() -> None:
    """TypeScript: function_declaration как дочерний узел program."""
    code = b"function foo(): number {\n    return 42;\n}\n"
    tree = parse_code(code, "typescript")
    assert isinstance(tree, Tree)
    assert tree.root_node.type == "program"
    child_types = [c.type for c in tree.root_node.children]
    assert "function_declaration" in child_types


def test_parse_tsx() -> None:
    """TSX: JSX-элемент распознаётся (другая грамматика, не typescript)."""
    code = b"const x = <div>hello</div>;\n"
    tree = parse_code(code, "tsx")
    assert isinstance(tree, Tree)
    assert tree.root_node.type == "program"


def test_parse_go() -> None:
    """Go: function_declaration в source_file."""
    code = b"package main\n\nfunc main() {\n    println(42)\n}\n"
    tree = parse_code(code, "go")
    assert isinstance(tree, Tree)
    assert tree.root_node.type == "source_file"
    child_types = [c.type for c in tree.root_node.children]
    assert "function_declaration" in child_types


def test_parse_java() -> None:
    """Java: class_declaration в program."""
    code = b"class Foo {\n    void bar() {}\n}\n"
    tree = parse_code(code, "java")
    assert isinstance(tree, Tree)
    assert tree.root_node.type == "program"
    child_types = [c.type for c in tree.root_node.children]
    assert "class_declaration" in child_types


def test_parse_sql() -> None:
    """SQL: statement в program."""
    code = b"SELECT * FROM users WHERE id = 1;\n"
    tree = parse_code(code, "sql")
    assert isinstance(tree, Tree)
    assert tree.root_node.type == "program"
    child_types = [c.type for c in tree.root_node.children]
    assert "statement" in child_types


# ---------------------------------------------------------------------------
# Типы TypeScript vs TSX — разные грамматики
# ---------------------------------------------------------------------------


def test_typescript_and_tsx_are_different_parsers() -> None:
    """TypeScript и TSX используют разные грамматики."""
    ts_parser = get_parser("typescript")
    tsx_parser = get_parser("tsx")
    assert ts_parser is not tsx_parser
    assert ts_parser.language != tsx_parser.language


# ---------------------------------------------------------------------------
# Кэширование парсеров
# ---------------------------------------------------------------------------


def test_parser_is_cached() -> None:
    """Парсер кэшируется — повторный вызов возвращает тот же экземпляр."""
    p1 = get_parser("python")
    p2 = get_parser("python")
    assert p1 is p2


def test_each_language_has_distinct_parser() -> None:
    """Каждый язык получает свой парсер."""
    parsers = [get_parser(lang) for lang in SUPPORTED_LANGUAGES]
    for i, p1 in enumerate(parsers):
        for j, p2 in enumerate(parsers):
            if i != j:
                assert p1 is not p2, f"Same parser for different languages at {i},{j}"


# ---------------------------------------------------------------------------
# Обработка ошибок
# ---------------------------------------------------------------------------


def test_get_parser_unsupported_language() -> None:
    """Неподдерживаемый язык → ValueError."""
    with pytest.raises(ValueError, match="Неподдерживаемый язык"):
        get_parser("ruby")


def test_parse_code_unsupported_language() -> None:
    """parse_code с неподдерживаемым языком → ValueError."""
    with pytest.raises(ValueError, match="Неподдерживаемый язык"):
        parse_code(b"x = 1", "ruby")


# ---------------------------------------------------------------------------
# Согласованность констант
# ---------------------------------------------------------------------------


def test_supported_languages_matches_loaders() -> None:
    """SUPPORTED_LANGUAGES соответствует ключам _LANGUAGE_LOADERS."""
    assert SUPPORTED_LANGUAGES == frozenset(_LANGUAGE_LOADERS.keys())


def test_supported_extensions_covers_all_languages() -> None:
    """Каждый язык имеет хотя бы одно расширение."""
    mapped_languages = set(_EXTENSION_TO_LANGUAGE.values())
    assert mapped_languages == set(SUPPORTED_LANGUAGES)


# ---------------------------------------------------------------------------
# Интеграция: detect_language → parse_code
# ---------------------------------------------------------------------------


def test_detect_then_parse_python() -> None:
    """Полный путь: detect_language → parse_code."""
    source = b"def add(a, b):\n    return a + b\n"
    language = detect_language("math_utils.py")
    assert language is not None
    tree = parse_code(source, language)
    fn_nodes = [c for c in tree.root_node.children if c.type == "function_definition"]
    assert len(fn_nodes) == 1
    text = fn_nodes[0].text
    assert text is not None
    assert b"def add" in text


def test_detect_then_parse_unsupported() -> None:
    """Неподдерживаемый файл → detect_language None, parse не вызывается."""
    language = detect_language("README.md")
    assert language is None


# ---------------------------------------------------------------------------
# Очистка кэша парсеров между тестами
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_parser_cache() -> Generator[None]:
    """Очищает кэш парсеров после каждого теста."""
    yield
    _parsers.clear()
