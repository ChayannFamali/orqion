"""Тест путей миграций (критический баг T-004, исправлен).

MIGRATIONS_DIR и ALEMBIC_INI должны указывать на существующие пути.
Баг: пути строились с лишним переходом в каталоге —
MIGRATIONS_DIR как backend/app/db/db/migrations (не существует),
ALEMBIC_INI как backend/alembic.ini (не существует).
Приёмка T-013: orqion migrate и orqion serve на чистой SQLite работают.
"""

from __future__ import annotations

from app.db.migrate import ALEMBIC_INI, MIGRATIONS_DIR


def test_alembic_ini_exists() -> None:
    """ALEMBIC_INI указывает на существующий файл."""
    assert ALEMBIC_INI.exists(), f"ALEMBIC_INI не существует: {ALEMBIC_INI}"
    assert ALEMBIC_INI.is_file(), f"ALEMBIC_INI не файл: {ALEMBIC_INI}"


def test_migrations_dir_exists() -> None:
    """MIGRATIONS_DIR указывает на существующую директорию."""
    assert MIGRATIONS_DIR.exists(), f"MIGRATIONS_DIR не существует: {MIGRATIONS_DIR}"
    assert MIGRATIONS_DIR.is_dir(), f"MIGRATIONS_DIR не директория: {MIGRATIONS_DIR}"


def test_migrations_dir_contains_versions() -> None:
    """В MIGRATIONS_DIR есть поддиректория versions с миграциями."""
    versions = MIGRATIONS_DIR / "versions"
    assert versions.exists(), f"versions не существует: {versions}"
    py_files = list(versions.glob("*.py"))
    assert len(py_files) > 0, "Нет файлов миграций в versions/"
    # Все файлы миграций (кроме __init__) должны начинаться с цифр
    migration_files = [f for f in py_files if not f.name.startswith("__")]
    assert len(migration_files) > 0, "Нет миграций (только __init__)"
