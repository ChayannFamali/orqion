"""Restore: восстановление из backup архива (T-426).

Профиль minimal: SQLite + sqlite-vec + локальная ФС.

Safety: restore на непустой инстанс без --force — отказ.
"Пустой" = нет пользовательских данных (documents, corpora, usage_events, etc.).
Seed-данные (workspace, roles, routing rules) не считаются — они будут перезаписаны.

Secret key: backup не включает .secret_key. Provider.api_key_enc зашифрован
AES-GCM с ключом из исходного инстанса. При restore на другой инстанс — warning.

Известное ограничение: backup во время активной индексации может дать рассинхрон
между DB и vec.db. Восстановимо переиндексацией (build_index_version).

Usage:
    python backend/scripts/restore.py [--input PATH] [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import models  # noqa: F401 — регистрация таблиц в metadata
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Таблицы с пользовательскими данными — presence здесь блокирует restore без --force.
USER_DATA_TABLES = (
    "document",
    "corpus",
    "conversation",
    "message",
    "usage_event",
    "usage_daily",
    "chunk",
    "index_version",
    "eval_set",
    "eval_item",
    "eval_run",
    "trace",
    "span",
    "audit_log",
)

ARCHIVE_VERSION = 1


@dataclass(frozen=True)
class RestoreResult:
    """Результат restore."""

    restored: bool
    db_table_count: int
    blob_count: int
    blob_total_bytes: int
    orqion_version: str
    vec_method: str
    warnings: list[str] = field(default_factory=list)


def _to_async_url(url: str) -> str:
    """Преобразует URL в async-вариант."""
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://")
    return url


async def _check_target_empty(
    settings: Settings,
) -> tuple[bool, list[str]]:
    """Проверяет, пуст ли target по пользовательским данным.

    Returns:
        (is_empty, blocking_tables) — True если пуст, иначе False + список таблиц.
    """
    db_url = _to_async_url(settings.database_url)
    if not db_url.startswith("sqlite"):
        raise RuntimeError(
            f"Restore поддерживает только SQLite, текущий URL: {settings.database_url}"
        )

    engine: AsyncEngine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:

            def _get_table_names(sync_conn: Any) -> list[str]:
                names: list[str] = inspect(sync_conn).get_table_names()
                return names

            existing_tables: list[str] = await conn.run_sync(_get_table_names)

            blocking: list[str] = []
            for table_name in USER_DATA_TABLES:
                if table_name not in existing_tables:
                    continue
                result = await conn.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1"))
                if result.fetchone() is not None:
                    blocking.append(table_name)

            return len(blocking) == 0, blocking
    finally:
        await engine.dispose()


def _extract_archive(
    archive_path: str,
    dest_dir: Path,
) -> dict[str, Any]:
    """Распаковывает архив и возвращает manifest."""
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(path=str(dest_dir))

    manifest_path = dest_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("Архив не содержит manifest.json")

    manifest_data: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest_data


async def restore(
    settings: Settings,
    input_path: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> RestoreResult:
    """Восстанавливает orqion из backup архива.

    Args:
        settings: Настройки приложения.
        input_path: Путь к архиву.
        force: Перезаписать непустой target.
        dry_run: Показать содержимое архива без записи.

    Returns:
        RestoreResult с метаданными.
    """
    warnings: list[str] = []

    # Проверка профиля
    if settings.profile != "minimal":
        raise RuntimeError(
            f"Restore поддерживает только профиль 'minimal', текущий: '{settings.profile}'."
        )

    # Проверка архива
    if not os.path.exists(input_path):
        raise RuntimeError(f"Архив не найден: {input_path}")

    # Извлекаем paths
    db_url = settings.database_url
    if db_url.startswith("sqlite+aiosqlite://"):
        db_path = db_url[len("sqlite+aiosqlite://") :]
    elif db_url.startswith("sqlite://"):
        db_path = db_url[len("sqlite://") :]
    else:
        raise RuntimeError(
            f"Restore поддерживает только SQLite, текущий URL: {settings.database_url}"
        )

    # Windows absolute path: sqlite:///C:/path → /C:/path → C:/path
    if len(db_path) > 2 and db_path[0] == "/" and db_path[1].isalpha() and db_path[2] == ":":
        db_path = db_path[1:]

    vec_path = settings.vector_store_path
    blob_path = settings.blob_store_path

    # Распаковка во temp
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        manifest = _extract_archive(input_path, tmp)

        # Проверка manifest
        if manifest.get("archive_version") != ARCHIVE_VERSION:
            raise RuntimeError(
                f"Неподдерживаемая версия архива: {manifest.get('archive_version')}, "
                f"ожидается {ARCHIVE_VERSION}"
            )

        if manifest.get("profile") != "minimal":
            raise RuntimeError(
                f"Архив для профиля '{manifest.get('profile')}', "
                "restore поддерживает только 'minimal'"
            )

        # Проверка целостности
        db_snapshot = tmp / "db.sqlite"
        vec_snapshot = tmp / "vec.sqlite"
        blob_snapshot = tmp / "blobs"

        if not db_snapshot.exists():
            raise RuntimeError("Архив не содержит db.sqlite")

        # Подсчёт содержимого архива
        archive_table_count = manifest.get("db_table_count", 0)
        archive_blob_count = manifest.get("blob_count", 0)
        archive_blob_bytes = manifest.get("blob_total_bytes", 0)
        orqion_version = manifest.get("orqion_version", "unknown")
        vec_method = manifest.get("vec_method", "unknown")

        # Версия приложения
        if orqion_version != "0.1.0":
            warnings.append(f"Архив создан orqion {orqion_version}, текущая версия 0.1.0")

        # Проверка: есть ли Provider с api_key_enc
        if db_snapshot.exists() and db_snapshot.stat().st_size > 0:
            conn = sqlite3.connect(str(db_snapshot))
            try:
                cursor = conn.execute(
                    "SELECT count(*) FROM provider WHERE api_key_enc IS NOT NULL "
                    "AND api_key_enc != ''"
                )
                provider_with_keys = cursor.fetchone()[0]
                if provider_with_keys > 0:
                    warnings.append(
                        f"В архиве {provider_with_keys} провайдеров с зашифрованными API-ключами. "
                        "Ключи зашифрованы AES-GCM с ключом исходного инстанса. "
                        "Установите ORQION_SECRET_KEY в значение исходного инстанса "
                        "или введите credentials заново через UI."
                    )
                conn.close()
            except sqlite3.Error:
                conn.close()

        if dry_run:
            # Вывод содержимого без записи
            return RestoreResult(
                restored=False,
                db_table_count=archive_table_count,
                blob_count=archive_blob_count,
                blob_total_bytes=archive_blob_bytes,
                orqion_version=orqion_version,
                vec_method=vec_method,
                warnings=warnings,
            )

        # Safety check: target должен быть пустым по пользовательским данным
        is_empty, blocking_tables = await _check_target_empty(settings)
        if not is_empty and not force:
            raise RuntimeError(
                f"Целевой инстанс содержит пользовательские данные в таблицах: "
                f"{blocking_tables}. Используйте --force для перезаписи."
            )

        # Если --force: удаляем существующие данные
        if force:
            if os.path.exists(db_path):
                os.remove(db_path)
            # WAL и SHM файлы
            for suffix in ("-wal", "-shm"):
                wal_path = db_path + suffix
                if os.path.exists(wal_path):
                    os.remove(wal_path)

            if os.path.exists(vec_path):
                os.remove(vec_path)
            for suffix in ("-wal", "-shm"):
                wal_path = vec_path + suffix
                if os.path.exists(wal_path):
                    os.remove(wal_path)

            if os.path.exists(blob_path):
                shutil.rmtree(blob_path)

        # Восстановление DB
        db_dest = Path(db_path)
        db_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(db_snapshot), str(db_dest))

        # Восстановление vector store
        if vec_snapshot.stat().st_size > 0:
            vec_dest = Path(vec_path)
            vec_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(vec_snapshot), str(vec_dest))

        # Восстановление blob store
        if blob_snapshot.exists():
            blob_dest = Path(blob_path)
            blob_dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(blob_snapshot), str(blob_dest), dirs_exist_ok=True)

        return RestoreResult(
            restored=True,
            db_table_count=archive_table_count,
            blob_count=archive_blob_count,
            blob_total_bytes=archive_blob_bytes,
            orqion_version=orqion_version,
            vec_method=vec_method,
            warnings=warnings,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore orqion from backup archive")
    parser.add_argument(
        "--input",
        required=True,
        help="Путь к backup архиву (.tar.gz)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перезаписать непустой target.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать содержимое архива без записи.",
    )
    args = parser.parse_args()

    settings = Settings()
    print(f"Restore: profile={settings.profile}, db={settings.database_url}")
    print(f"  vector_store={settings.vector_store_path}")
    print(f"  blob_store={settings.blob_store_path}")
    print(f"  archive={args.input}")
    if args.dry_run:
        print("  [DRY RUN]")
    if args.force:
        print("  [FORCE]")

    try:
        result = asyncio_run_restore(settings, args.input, force=args.force, dry_run=args.dry_run)
    except RuntimeError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)

    prefix = "[DRY RUN] " if args.dry_run and not result.restored else ""
    print(f"\n=== orqion: restore {prefix}complete ===")
    print(f"Restored: {result.restored}")
    print(f"DB tables: {result.db_table_count}")
    print(f"Blobs: {result.blob_count} ({result.blob_total_bytes:,} bytes)")
    print(f"Orqion version: {result.orqion_version}")
    print(f"Vector store method: {result.vec_method}")
    if result.warnings:
        print("Warnings:")
        for w in result.warnings:
            print(f"  - {w}")
    print("=== Done ===")


def asyncio_run_restore(
    settings: Settings,
    input_path: str,
    *,
    force: bool,
    dry_run: bool,
) -> RestoreResult:
    """Обёртка для asyncio.run в sync main()."""
    import asyncio

    return asyncio.run(restore(settings, input_path, force=force, dry_run=dry_run))


if __name__ == "__main__":
    main()
