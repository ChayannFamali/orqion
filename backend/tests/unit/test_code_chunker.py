"""Тесты чанкинга кода по символам (T-209, S-23).

Проверки:
- Каждый поддерживаемый язык: функция и класс как отдельные чанки
- Метод внутри класса: parent = имя класса
- Импорты в метаданных чанка
- Сигнатура в метаданных
- Крупный символ дробится, сигнатура дублируется в каждом фрагменте
- Ни один чанк не содержит оборванной функции
- Файл без грамматики — fallback с пометкой
- CODE_CHUNKER_VERSION в метаданных
- SQL не обрабатывается (T-210)
"""

from __future__ import annotations

import tiktoken
from app.rag.code_chunker import CODE_CHUNKER_VERSION, chunk_code


def _enc() -> tiktoken.Encoding:
    return tiktoken.encoding_for_model("gpt-4")


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def test_python_function_and_class() -> None:
    """Python: функция и класс без методов — отдельные чанки."""
    code = b"def foo():\n    return 42\n\nclass Bar:\n    pass\n"
    chunks = chunk_code(code, "test.py")

    assert len(chunks) == 2
    assert chunks[0].symbol == "foo"
    assert chunks[0].language == "python"
    assert chunks[1].symbol == "Bar"
    assert chunks[1].language == "python"


def test_python_method_parent() -> None:
    """Python: методы внутри класса — каждый отдельный чанк, parent = имя класса."""
    code = b"class Foo:\n    def bar(self):\n        return 1\n\n    def baz(self):\n        return 2\n"
    chunks = chunk_code(code, "test.py")

    assert len(chunks) == 2
    assert chunks[0].symbol == "bar"
    assert chunks[0].parent == "Foo"
    assert chunks[1].symbol == "baz"
    assert chunks[1].parent == "Foo"


def test_python_class_without_methods_is_atomic() -> None:
    """Python: класс без методов (поля, docstring) — один чанк целиком (ADR-9)."""
    code = b"class Point:\n    x: int = 0\n    y: int = 0\n"
    chunks = chunk_code(code, "test.py")

    assert len(chunks) == 1
    assert chunks[0].symbol == "Point"
    assert chunks[0].parent is None


def test_python_imports() -> None:
    """Python: импорты в метаданных."""
    code = b"import os\nfrom typing import List\n\ndef foo():\n    pass\n"
    chunks = chunk_code(code, "test.py")

    assert len(chunks) >= 1
    assert "import os" in chunks[0].imports
    assert "from typing import List" in chunks[0].imports


def test_python_signature() -> None:
    """Python: сигнатура в метаданных."""
    code = b"def foo(x: int) -> str:\n    return str(x)\n"
    chunks = chunk_code(code, "test.py")

    assert len(chunks) == 1
    assert chunks[0].signature == "def foo(x: int) -> str:"


# ---------------------------------------------------------------------------
# C++
# ---------------------------------------------------------------------------


def test_cpp_function_and_class() -> None:
    """C++: функция и класс без методов — отдельные чанки."""
    code = b"int main() {\n    return 0;\n}\n\nclass Bar {\npublic:\n    int x;\n};\n"
    chunks = chunk_code(code, "test.cpp")

    symbols = [c.symbol for c in chunks]
    assert "main" in symbols
    assert "Bar" in symbols


def test_cpp_class_parent() -> None:
    """C++: методы внутри класса — каждый отдельный чанк, parent = имя класса."""
    code = b"class Bar {\npublic:\n    int foo() { return 1; }\n    void baz() { return; }\n};\n"
    chunks = chunk_code(code, "test.cpp")

    assert len(chunks) == 2
    assert chunks[0].symbol == "foo"
    assert chunks[0].parent == "Bar"
    assert chunks[1].symbol == "baz"
    assert chunks[1].parent == "Bar"


# ---------------------------------------------------------------------------
# TypeScript
# ---------------------------------------------------------------------------


def test_typescript_function_and_class() -> None:
    """TypeScript: функция и класс без методов — отдельные чанки."""
    code = b"function foo(): number {\n    return 42;\n}\n\nclass Bar {\n    x: number = 0;\n}\n"
    chunks = chunk_code(code, "test.ts")

    symbols = [c.symbol for c in chunks]
    assert "foo" in symbols
    assert "Bar" in symbols


def test_typescript_method_parent() -> None:
    """TypeScript: метод внутри класса — отдельный чанк, parent = имя класса."""
    code = b"class Bar {\n    method() {}\n    getter() { return 1; }\n}\n"
    chunks = chunk_code(code, "test.ts")

    assert len(chunks) == 2
    assert chunks[0].symbol == "method"
    assert chunks[0].parent == "Bar"
    assert chunks[1].symbol == "getter"
    assert chunks[1].parent == "Bar"


def test_tsx_grammar_selected() -> None:
    """TSX: файл .tsx использует tsx-грамматику."""
    code = b"const x = <div>hello</div>;\n\nfunction foo() {\n    return 42;\n}\n"
    chunks = chunk_code(code, "test.tsx")

    assert len(chunks) >= 1
    assert chunks[0].language == "tsx"


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def test_go_function_and_type() -> None:
    """Go: функция и type declaration — отдельные чанки."""
    code = b"package main\n\nfunc foo() {\n    println(42)\n}\n\ntype Bar struct {\n    x int\n}\n"
    chunks = chunk_code(code, "test.go")

    symbols = [c.symbol for c in chunks]
    assert "foo" in symbols
    assert "Bar" in symbols


def test_go_method_value_receiver_parent() -> None:
    """Go: метод с value receiver — parent = имя типа из receiver."""
    code = (
        b"package main\n\n"
        b"type Bar struct {\n    x int\n}\n\n"
        b"func (b Bar) method() {\n    println(b.x)\n}\n"
    )
    chunks = chunk_code(code, "test.go")

    method_chunks = [c for c in chunks if c.symbol == "method"]
    assert len(method_chunks) == 1
    assert method_chunks[0].parent == "Bar"


def test_go_method_pointer_receiver_parent() -> None:
    """Go: метод с pointer receiver — parent = имя типа, * отброшен."""
    code = (
        b"package main\n\n"
        b"type Server struct {\n    x int\n}\n\n"
        b"func (s *Server) Method() {\n    println(s.x)\n}\n"
    )
    chunks = chunk_code(code, "test.go")

    method_chunks = [c for c in chunks if c.symbol == "Method"]
    assert len(method_chunks) == 1
    assert method_chunks[0].parent == "Server"


def test_go_imports() -> None:
    """Go: import declaration в метаданных."""
    code = b'package main\n\nimport (\n    "fmt"\n    "os"\n)\n\nfunc main() {}\n'
    chunks = chunk_code(code, "test.go")

    assert len(chunks) >= 1
    assert any("fmt" in imp for imp in chunks[0].imports)


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


def test_java_class_and_method() -> None:
    """Java: методы внутри класса — каждый отдельный чанк, parent = имя класса."""
    code = b"class Foo {\n    void bar() {}\n    int baz() { return 1; }\n}\n"
    chunks = chunk_code(code, "test.java")

    assert len(chunks) == 2
    assert chunks[0].symbol == "bar"
    assert chunks[0].parent == "Foo"
    assert chunks[1].symbol == "baz"
    assert chunks[1].parent == "Foo"


def test_java_imports() -> None:
    """Java: import declarations в метаданных."""
    code = b"import java.util.List;\nimport com.foo.Bar;\n\nclass X {}\n"
    chunks = chunk_code(code, "test.java")

    assert len(chunks) >= 1
    assert "import java.util.List;" in chunks[0].imports
    assert "import com.foo.Bar;" in chunks[0].imports


# ---------------------------------------------------------------------------
# Дробление крупных символов
# ---------------------------------------------------------------------------


def test_large_symbol_split() -> None:
    """Крупный символ (>800 токенов) дробится на фрагменты."""
    # Генерируем функцию с ~1000+ токенов
    body_lines = [f"    x = {i}" for i in range(500)]
    code = "def big_function():\n" + "\n".join(body_lines) + "\n"
    chunks = chunk_code(code.encode(), "big.py")

    assert len(chunks) > 1
    enc = _enc()
    for chunk in chunks:
        token_count = len(enc.encode(chunk.text))
        assert token_count <= 800, f"Chunk {chunk.symbol}: {token_count} > 800"


def test_large_symbol_signature_duplicated() -> None:
    """При дроблении сигнатура дублируется в каждом фрагменте."""
    body_lines = [f"    x = {i}" for i in range(500)]
    code = "def big_function():\n" + "\n".join(body_lines) + "\n"
    chunks = chunk_code(code.encode(), "big.py")

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.signature == "def big_function():"
        # Сигнатура дублируется в тексте каждого фрагмента
        assert "def big_function():" in chunk.text


def test_large_symbol_file_path_in_each_fragment() -> None:
    """При дроблении file_path дублируется в метаданных каждого фрагмента."""
    body_lines = [f"    x = {i}" for i in range(500)]
    code = "def big_function():\n" + "\n".join(body_lines) + "\n"
    chunks = chunk_code(code.encode(), "src/big.py")

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.file_path == "src/big.py"


def test_large_symbol_name_preserved() -> None:
    """Все фрагменты дроблёного символа имеют одно имя."""
    body_lines = [f"    x = {i}" for i in range(500)]
    code = "def big_function():\n" + "\n".join(body_lines) + "\n"
    chunks = chunk_code(code.encode(), "big.py")

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.symbol == "big_function"


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def test_fallback_unsupported_extension() -> None:
    """Файл без грамматики — fallback с пометкой."""
    code = b"line one\nline two\nline three\n" * 100
    chunks = chunk_code(code, "file.rb")

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.language is None
        assert chunk.symbol is None
        assert chunk.parent is None
        assert chunk.signature is None
        assert chunk.imports == []
        assert chunk.meta.get("fallback") is True


def test_fallback_no_illusion_of_structure() -> None:
    """Fallback не создаёт иллюзию структуры — symbol = None."""
    code = b"some text\n" * 200
    chunks = chunk_code(code, "file.txt")

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.symbol is None
        assert chunk.meta.get("fallback") is True


def test_fallback_has_language_and_version() -> None:
    """Fallback-чанки содержат language=None и CODE_CHUNKER_VERSION."""
    code = b"some text\n" * 200
    chunks = chunk_code(code, "file.txt")

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.language is None
        assert chunk.meta["code_chunker_version"] == CODE_CHUNKER_VERSION


def test_fallback_token_limit() -> None:
    """Fallback-чанки не превышают 500 токенов."""
    code = b"word " * 2000
    chunks = chunk_code(code, "file.txt")

    enc = _enc()
    for chunk in chunks:
        token_count = len(enc.encode(chunk.text))
        assert token_count <= 500, f"Fallback chunk: {token_count} > 500"


# ---------------------------------------------------------------------------
# Метаданные и версия
# ---------------------------------------------------------------------------


def test_code_chunker_version_in_meta() -> None:
    """CODE_CHUNKER_VERSION в метаданных каждого чанка."""
    code = b"def foo():\n    return 42\n"
    chunks = chunk_code(code, "test.py")

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.meta["code_chunker_version"] == CODE_CHUNKER_VERSION


def test_start_end_line() -> None:
    """start_line и end_line корректны."""
    code = b"def foo():\n    return 42\n\ndef bar():\n    return 1\n"
    chunks = chunk_code(code, "test.py")

    assert len(chunks) == 2
    assert chunks[0].start_line == 0
    assert chunks[0].end_line == 1
    assert chunks[1].start_line == 3
    assert chunks[1].end_line == 4


# ---------------------------------------------------------------------------
# SQL не обрабатывается (T-210)
# ---------------------------------------------------------------------------


def test_sql_falls_back_to_text_chunking() -> None:
    """SQL-файлы обрабатываются fallback-путем до T-210."""
    code = b"SELECT * FROM users;\nINSERT INTO logs VALUES (1);\n"
    chunks = chunk_code(code, "test.sql")

    # SQL не входит в _SYMBOL_TYPES для T-209
    for chunk in chunks:
        assert chunk.language is None
        assert chunk.meta.get("fallback") is True


# ---------------------------------------------------------------------------
# Пустой файл
# ---------------------------------------------------------------------------


def test_empty_file() -> None:
    """Пустой файл — пустой список чанков."""
    chunks = chunk_code(b"", "test.py")
    assert chunks == []


# ---------------------------------------------------------------------------
# Файл с импортами, но без символов
# ---------------------------------------------------------------------------


def test_imports_only_no_symbols() -> None:
    """Файл только с импортами, без функций/классов — нет чанков."""
    code = b"import os\nimport sys\n"
    chunks = chunk_code(code, "test.py")
    assert chunks == []


# ---------------------------------------------------------------------------
# Ни один чанк не содержит оборванной функции
# ---------------------------------------------------------------------------


def test_no_truncated_function() -> None:
    """Каждый чанк содержит целую функцию или класс, не оборванный фрагмент."""
    code = b"def foo():\n    return 42\n\ndef bar():\n    x = 1\n    return x\n"
    chunks = chunk_code(code, "test.py")

    assert len(chunks) == 2
    # Каждый чанк содержит完整ное определение функции
    assert "def foo()" in chunks[0].text
    assert "return 42" in chunks[0].text
    assert "def bar()" in chunks[1].text
    assert "return x" in chunks[1].text
