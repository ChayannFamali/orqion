"""Перенос данных SQLite → PostgreSQL (T-410, BUG-005).

Читает все таблицы из SQLite, записывает в PostgreSQL в порядке FK-зависимостей
(Base.metadata.sorted_tables — топологическая сортировка SQLAlchemy).

Не переносит FTS5/vec0 таблицы — они в отдельном SQLite-файле векторного store.

Idempotent: проверяет, что целевая БД пуста.

Usage:
    python backend/scripts/migrate_sqlite_to_postgres.py \
        --source "sqlite:///./orqion.db" \
        --dest "postgresql://user:password@localhost:5432/orqion"
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.db import models  # noqa: F401 — регистрация таблиц в metadata
from app.db.base import Base
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

# Таблицы векторного store — живут в отдельном SQLite-файле, не переносятся.
_SKIP_TABLES = {"fts_chunks", "vec_chunks", "vec_chunk_map"}


def _to_async_url(url: str) -> str:
    """Преобразует URL в async-вариант для SQLAlchemy."""
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://")
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return url.replace("postgresql://", "postgresql+asyncpg://")
    return url


async def migrate(source_url: str, dest_url: str) -> dict[str, int]:
    """Копирует данные из SQLite в PostgreSQL.

    Возвращает {table_name: row_count} для каждой перенесённой таблицы.
    Проверяет, что целевая БД пуста.
    """
    source_async_url = _to_async_url(source_url)
    dest_async_url = _to_async_url(dest_url)

    source_engine = create_async_engine(source_async_url)
    dest_engine = create_async_engine(dest_async_url)

    try:
        # Проверка: целевая БД должна быть пустой (схема создана, но без данных).
        # Схема создаётся через `alembic upgrade head` (или `create_all`),
        # после чего запускается этот скрипт. Проверяем row counts, не table existence.
        async with dest_engine.connect() as conn:
            non_empty_tables: list[str] = []
            for table in Base.metadata.sorted_tables:
                if table.name in _SKIP_TABLES:
                    continue
                result = await conn.execute(select(table).limit(1))
                if result.fetchone() is not None:
                    non_empty_tables.append(table.name)
            if non_empty_tables:
                raise RuntimeError(
                    f"Целевая БД не пуста. Таблицы с данными: {non_empty_tables}. "
                    "Очистите БД или используйте пустую."
                )

        # Получаем сортированные таблицы из метаданных (топологическая сортировка FK)
        sorted_tables = [t for t in Base.metadata.sorted_tables if t.name not in _SKIP_TABLES]

        counts: dict[str, int] = {}

        for table in sorted_tables:
            async with source_engine.connect() as src_conn:
                result = await src_conn.execute(select(table))
                rows = result.fetchall()

                if not rows:
                    counts[table.name] = 0
                    continue

                # Записываем пачками
                async with dest_engine.begin() as dst_conn:
                    await dst_conn.execute(
                        table.insert(),
                        [dict(row._mapping) for row in rows],
                    )

                counts[table.name] = len(rows)
                print(f"  {table.name}: {len(rows)} строк")

        return counts

    finally:
        await source_engine.dispose()
        await dest_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Перенос данных SQLite → PostgreSQL")
    parser.add_argument(
        "--source",
        required=True,
        help="URL источника SQLite, например: sqlite:///./orqion.db",
    )
    parser.add_argument(
        "--dest",
        required=True,
        help="URL приёмника PostgreSQL, например: postgresql://user:pass@host:5432/db",
    )
    args = parser.parse_args()

    print(f"Миграция: {args.source} → {args.dest}")
    print("Проверка целевой БД...")

    try:
        counts = asyncio.run(migrate(args.source, args.dest))
    except RuntimeError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)

    total = sum(counts.values())
    print(f"\nГотово. Перенесено {total} строк в {len(counts)} таблицах.")
    for name, count in sorted(counts.items()):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
