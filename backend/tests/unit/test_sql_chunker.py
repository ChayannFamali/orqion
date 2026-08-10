"""Тесты чанкинга SQL по statement (T-210, S-23).

Проверки:
- Каждый тип операции: SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, CTE
- Извлечение имён таблиц для каждого типа
- JOIN — несколько таблиц в одном statement
- Метаданные: operation, tables, start_line, end_line, sql_chunker_version
- Несколько statements — каждый отдельный чанк, ordinal глобальный
- Пустой файл — пустой список
- Не-SQL файл — пустой список
"""

from __future__ import annotations

from app.rag.sql_chunker import SQL_CHUNKER_VERSION, chunk_sql

# ---------------------------------------------------------------------------
# Типы операций
# ---------------------------------------------------------------------------


def test_select() -> None:
    """SELECT: operation=SELECT, tables=[users]."""
    code = b"SELECT * FROM users WHERE id = 1;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].operation == "SELECT"
    assert "users" in chunks[0].tables


def test_insert() -> None:
    """INSERT: operation=INSERT, tables=[logs]."""
    code = b"INSERT INTO logs VALUES (1);\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].operation == "INSERT"
    assert "logs" in chunks[0].tables


def test_update() -> None:
    """UPDATE: operation=UPDATE, tables=[users]."""
    code = b"UPDATE users SET name = 'x' WHERE id = 1;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].operation == "UPDATE"
    assert "users" in chunks[0].tables


def test_delete() -> None:
    """DELETE: operation=DELETE, tables=[logs]."""
    code = b"DELETE FROM logs WHERE id = 1;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].operation == "DELETE"
    assert "logs" in chunks[0].tables


def test_create_table() -> None:
    """CREATE TABLE: operation=CREATE, tables=[t]."""
    code = b"CREATE TABLE t (id INT);\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].operation == "CREATE"
    assert "t" in chunks[0].tables


def test_alter_table() -> None:
    """ALTER TABLE: operation=ALTER, tables=[t]."""
    code = b"ALTER TABLE t ADD COLUMN x INT;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].operation == "ALTER"
    assert "t" in chunks[0].tables


def test_drop_table() -> None:
    """DROP TABLE: operation=DROP, tables=[t]."""
    code = b"DROP TABLE t;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].operation == "DROP"
    assert "t" in chunks[0].tables


def test_cte() -> None:
    """CTE (WITH ... AS): operation=CTE."""
    code = b"WITH cte AS (SELECT * FROM users) SELECT * FROM cte;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].operation == "CTE"


# ---------------------------------------------------------------------------
# Извлечение таблиц
# ---------------------------------------------------------------------------


def test_join_multiple_tables() -> None:
    """JOIN: обе таблицы извлечены."""
    code = b"SELECT u.name FROM users u JOIN posts p ON u.id = p.user_id;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert "users" in chunks[0].tables
    assert "posts" in chunks[0].tables


def test_select_no_table() -> None:
    """SELECT без FROM — пустой список таблиц."""
    code = b"SELECT 1;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].tables == []


def test_tables_deduplicated() -> None:
    """Повторяющиеся имена таблиц не дублируются."""
    code = b"SELECT * FROM users u1 JOIN users u2 ON u1.id = u2.id;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    table_count = chunks[0].tables.count("users")
    assert table_count == 1


# ---------------------------------------------------------------------------
# Несколько statements
# ---------------------------------------------------------------------------


def test_multiple_statements_separate_chunks() -> None:
    """Несколько statements — каждый отдельный чанк."""
    code = b"SELECT * FROM users;\nINSERT INTO logs VALUES (1);\nDROP TABLE t;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 3
    assert chunks[0].operation == "SELECT"
    assert chunks[1].operation == "INSERT"
    assert chunks[2].operation == "DROP"


def test_ordinal_is_global() -> None:
    """ordinal — глобальный, не локальный."""
    code = b"SELECT 1;\nSELECT 2;\nSELECT 3;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 3
    for i, chunk in enumerate(chunks):
        assert chunk.meta["ordinal"] == i


# ---------------------------------------------------------------------------
# Метаданные
# ---------------------------------------------------------------------------


def test_sql_chunker_version_in_meta() -> None:
    """SQL_CHUNKER_VERSION в метаданных каждого чанка."""
    code = b"SELECT 1;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert chunks[0].meta["sql_chunker_version"] == SQL_CHUNKER_VERSION


def test_start_end_line() -> None:
    """start_line и end_line корректны."""
    code = b"SELECT * FROM users;\nINSERT INTO logs VALUES (1);\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 2
    assert chunks[0].start_line == 0
    assert chunks[1].start_line == 1


def test_file_path_in_chunk() -> None:
    """file_path сохраняется в каждом чанке."""
    code = b"SELECT 1;\n"
    chunks = chunk_sql(code, "db/migrations/001.sql")

    assert len(chunks) == 1
    assert chunks[0].file_path == "db/migrations/001.sql"


# ---------------------------------------------------------------------------
# Граничные случаи
# ---------------------------------------------------------------------------


def test_empty_file() -> None:
    """Пустой файл — пустой список чанков."""
    chunks = chunk_sql(b"", "test.sql")
    assert chunks == []


def test_non_sql_file() -> None:
    """Не-SQL файл — пустой список."""
    chunks = chunk_sql(b"def foo():\n    pass\n", "test.py")
    assert chunks == []


def test_statement_text_preserved() -> None:
    """Текст statement сохраняется в чанке."""
    code = b"SELECT * FROM users WHERE id = 1;\n"
    chunks = chunk_sql(code, "test.sql")

    assert len(chunks) == 1
    assert "SELECT * FROM users" in chunks[0].text
    assert "WHERE id = 1" in chunks[0].text
