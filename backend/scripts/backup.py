"""Backup: снимок БД + blob store + vector store в tar.gz архив (T-426).

Профиль minimal: SQLite + sqlite-vec + локальная ФС. Все компоненты — локальные файлы.
Для standard/full (PostgreSQL/Qdrant/S3) — не поддерживается, см. follow-up.

Согласованность снимка:
  - DB: VACUUM INTO через отдельное sync-соединение (консистентный снимок, не блокирует запись).
  - Vector store: VACUUM INTO через sync-соединение с загруженным sqlite-vec расширением.
    Если VACUUM INTO не сработает с vec0 — fallback: PRAGMA wal_checkpoint(TRUNCATE) + copy.
  - Blob store: копирование директории (файлы immutable, .tmp/ исключается).

Известное ограничение: backup во время активной индексации может дать рассинхрон
между DB и vec.db. Восстановимо переиндексацией (build_index_version).

Secret key: backup НЕ включает .secret_key. Provider.api_key_enc зашифрован AES-GCM
с ключом из исходного инстанса. При restore на другой инстанс без того же ключа —
credentials придется ввести заново через UI.

Usage:
    python backend/scripts/backup.py [--output PATH]
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings


@dataclass(frozen=True)
class BackupResult:
    """Результат backup."""

    archive_path: str
    archive_size_bytes: int
    db_table_count: int
    blob_count: int
    blob_total_bytes: int
    vec_method: str  # "vacuum_into" | "wal_checkpoint_copy"
    warnings: list[str]


def _loadable_path() -> str:
    """Возвращает путь к sqlite-vec loadable, с расширением под платформу."""
    import sqlite_vec

    path = str(sqlite_vec.loadable_path())
    if not os.path.exists(path):
        for ext in (".dll", ".so", ".dylib"):
            candidate = path + ext
            if os.path.exists(candidate):
                return str(candidate)
    return path


def _vacuum_into_with_extension(
    source_db_path: str,
    dest_path: str,
    load_vec: bool,
) -> str:
    """VACUUM INTO через sync sqlite3 connection.

    Если load_vec=True — загружает sqlite-vec расширение перед VACUUM.
    Возвращает "vacuum_into" при успехе.
    Raises RuntimeError если VACUUM INTO не удался.
    """
    conn = sqlite3.connect(source_db_path)
    try:
        if load_vec:
            conn.enable_load_extension(True)
            conn.load_extension(_loadable_path())

        conn.execute(f"VACUUM INTO '{dest_path}'")
        conn.close()
        return "vacuum_into"
    except Exception:
        conn.close()
        raise


def _wal_checkpoint_copy(source_db_path: str, dest_path: str) -> str:
    """Fallback: PRAGMA wal_checkpoint(TRUNCATE) + копирование файла.

    Консистентно, т.к. в standalone скрипте никто не пишет в vec.db.
    """
    conn = sqlite3.connect(source_db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except sqlite3.Error:
        conn.close()

    shutil.copy2(source_db_path, dest_path)
    return "wal_checkpoint_copy"


def _count_blobs(blob_dir: str) -> tuple[int, int]:
    """Считает файлы и суммарный размер в blob директории (исключая .tmp/)."""
    count = 0
    total_bytes = 0
    root = Path(blob_dir)
    if not root.exists():
        return 0, 0

    for path in root.rglob("*"):
        if path.is_file() and ".tmp" not in path.parts:
            count += 1
            total_bytes += path.stat().st_size

    return count, total_bytes


def _count_db_tables(db_path: str) -> int:
    """Считает количество таблиц в SQLite БД."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
        count: int = cursor.fetchone()[0]
        conn.close()
        return count
    except sqlite3.Error:
        conn.close()
        return 0


def backup(settings: Settings, output_path: str | None = None) -> BackupResult:
    """Создаёт backup архив.

    Args:
        settings: Настройки приложения (database_url, vector_store_path, etc.)
        output_path: Путь для архива. If None — default orqion-backup-{timestamp}.tar.gz.

    Returns:
        BackupResult с метаданными.
    """
    warnings: list[str] = []

    # Проверка профиля
    if settings.profile != "minimal":
        raise RuntimeError(
            f"Backup поддерживает только профиль 'minimal', текущий: '{settings.profile}'. "
            "Для standard/full используйте pg_dump / Qdrant snapshot / S3 sync."
        )

    # Извлекаем пути файлов из settings
    db_url = settings.database_url
    if db_url.startswith("sqlite+aiosqlite://"):
        db_path = db_url[len("sqlite+aiosqlite://") :]
    elif db_url.startswith("sqlite://"):
        db_path = db_url[len("sqlite://") :]
    else:
        raise RuntimeError(f"Backup поддерживает только SQLite, текущий URL: {db_url}")

    # Windows absolute path: sqlite:///C:/path → /C:/path → C:/path
    if len(db_path) > 2 and db_path[0] == "/" and db_path[1].isalpha() and db_path[2] == ":":
        db_path = db_path[1:]

    vec_path = settings.vector_store_path
    blob_path = settings.blob_store_path

    # Default output path
    if output_path is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        output_path = f"orqion-backup-{timestamp}.tar.gz"

    # Версия orqion
    try:
        import importlib.metadata

        orqion_version = importlib.metadata.version("orqion")
    except (ImportError, ValueError):
        orqion_version = "0.1.0"

    # Подсчёт метаданных
    blob_count, blob_bytes = _count_blobs(blob_path)
    table_count = _count_db_tables(db_path) if os.path.exists(db_path) else 0

    # Создаём temp директорию для сборки архива
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. DB: VACUUM INTO
        db_snapshot = tmp / "db.sqlite"
        _vacuum_into_with_extension(str(db_path), str(db_snapshot), load_vec=False)

        # 2. Vector store: VACUUM INTO с загруженным расширением, fallback к copy
        vec_snapshot = tmp / "vec.sqlite"
        vec_method = "vacuum_into"
        if os.path.exists(vec_path):
            try:
                _vacuum_into_with_extension(str(vec_path), str(vec_snapshot), load_vec=True)
            except (sqlite3.Error, OSError):
                vec_method = _wal_checkpoint_copy(str(vec_path), str(vec_snapshot))
                warnings.append(
                    "VACUUM INTO для vec.db не удался, использован fallback "
                    "(wal_checkpoint + copy)."
                )
        else:
            # vec.db не существует — пустой файл
            vec_snapshot.write_bytes(b"")
            vec_method = "no_vec_db"

        # 3. Blob store: копирование директории (без .tmp/)
        blob_dest = tmp / "blobs"
        if os.path.exists(blob_path):
            blob_dest.mkdir()
            root = Path(blob_path)
            for path in root.rglob("*"):
                if path.is_file() and ".tmp" not in path.parts:
                    rel = path.relative_to(root)
                    dest_file = blob_dest / rel
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest_file)

        # 4. Manifest
        manifest = {
            "archive_version": 1,
            "orqion_version": orqion_version,
            "profile": "minimal",
            "created_at": datetime.now(UTC).isoformat(),
            "db_table_count": table_count,
            "blob_count": blob_count,
            "blob_total_bytes": blob_bytes,
            "vec_method": vec_method,
            "secret_key_included": False,
        }
        (tmp / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # 5. tar.gz
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(str(output), "w:gz") as tar:
            tar.add(str(tmp / "manifest.json"), arcname="manifest.json")
            tar.add(str(db_snapshot), arcname="db.sqlite")
            tar.add(str(vec_snapshot), arcname="vec.sqlite")
            if blob_dest.exists():
                tar.add(str(blob_dest), arcname="blobs")

        archive_size = output.stat().st_size

    return BackupResult(
        archive_path=str(output),
        archive_size_bytes=archive_size,
        db_table_count=table_count,
        blob_count=blob_count,
        blob_total_bytes=blob_bytes,
        vec_method=vec_method,
        warnings=warnings,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup orqion (minimal profile)")
    parser.add_argument(
        "--output",
        default=None,
        help="Путь для архива. Default: orqion-backup-{timestamp}.tar.gz",
    )
    args = parser.parse_args()

    settings = Settings()
    print(f"Backup: profile={settings.profile}, db={settings.database_url}")
    print(f"  vector_store={settings.vector_store_path}")
    print(f"  blob_store={settings.blob_store_path}")

    try:
        result = backup(settings, output_path=args.output)
    except RuntimeError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n=== orqion: backup complete ===")
    print(f"Archive: {result.archive_path}")
    print(f"Size: {result.archive_size_bytes:,} bytes")
    print(f"DB tables: {result.db_table_count}")
    print(f"Blobs: {result.blob_count} ({result.blob_total_bytes:,} bytes)")
    print(f"Vector store method: {result.vec_method}")
    if result.warnings:
        print("Warnings:")
        for w in result.warnings:
            print(f"  - {w}")
    print("=== Done ===")


if __name__ == "__main__":
    main()
