"""Git-репозиторий как источник ingestion (T-422).

flow: git URL/локальный путь → shallow clone (dulwich, depth=1) →
перебор файлов по маскам расширений → upload_document (source_type="git") →
существующий ingestion pipeline (T-204/T-206/T-207/T-209/T-210).

ADR-7: blob store — источник правды. Клон — временный артефакт, удаляется
после обработки. Не хранит .git с историей.

ADR-2: dulwich — dual Apache-2.0 OR GPL-2.0-or-later, используется под Apache-2.0.
Pure Python, не требует system git в PATH (N-1: minimal профиль автономен).

§14.2: инкрементальная переиндексация (git diff между запусками) — отложена.
Повторный запуск: sha256-дедупликация на уровне Document (corpus_id, sha256),
DuplicateDocument catch → skip. Полный перескан, O(n) с O(1) per file.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BadRequest, OrqionError
from app.rag.blob import BlobStore
from app.rag.service import upload_document

logger = logging.getLogger(__name__)

# Объединённый список расширений: документы + код + SQL.
# index_builder._CODE_EXTENSIONS + _SQL_EXTENSIONS + документы.
DEFAULT_EXTENSIONS: list[str] = [
    # Документы
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".md",
    ".txt",
    # Код (ADR-9: tree-sitter)
    ".py",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hpp",
    ".ts",
    ".tsx",
    ".go",
    ".java",
    # SQL
    ".sql",
]

# Лимит размера клона по умолчанию: 500 MB (рабочее дерево + .git).
# Защита от случайного клонирования монорепо на несколько GB.
DEFAULT_MAX_CLONE_SIZE_MB: int = 500

# Таймаут на clone по умолчанию: 120 секунд.
DEFAULT_CLONE_TIMEOUT_SECONDS: int = 120

# Размер чанка для чтения файлов с диска в BlobStore.
_READ_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class GitIngestResult:
    """Результат ingestion git-репозитория."""

    total_files: int
    ingested: int
    skipped: int
    failed: int
    errors: list[str] = field(default_factory=list)


async def ingest_git_repository(
    session: AsyncSession,
    blob_store: BlobStore,
    *,
    workspace_id: str,
    corpus_id: str,
    repo_url: str,
    allowed_extensions: list[str] | None = None,
    max_file_size_bytes: int = 50 * 1024 * 1024,
    depth: int = 1,
    clone_timeout_seconds: int = DEFAULT_CLONE_TIMEOUT_SECONDS,
    max_clone_size_mb: int = DEFAULT_MAX_CLONE_SIZE_MB,
) -> GitIngestResult:
    """Клонирует git-репозиторий и загружает файлы в корпус.

    1. Shallow clone (dulwich, depth=1) во временную директорию
    2. Проверка размера клона (защита от случайного клонирования огромного репо)
    3. Перебор файлов по маскам расширений
    4. Каждый файл — upload_document(source_type="git"), дедупликация по sha256
    5. Cleanup временной директории

    Args:
        depth: глубина clone. 1 = shallow (только последний commit).
               0 = полная история (не рекомендуется для ingestion).
        clone_timeout_seconds: таймаут на операцию clone.
        max_clone_size_mb: максимальный размер клона (рабочее дерево + .git).

    Returns:
        GitIngestResult со статистикой.
    """
    extensions = allowed_extensions if allowed_extensions is not None else DEFAULT_EXTENSIONS
    clone_dir: str | None = None
    total = 0
    ingested = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    try:
        clone_dir = await _shallow_clone(
            repo_url,
            depth=depth,
            timeout_seconds=clone_timeout_seconds,
            max_clone_size_mb=max_clone_size_mb,
        )

        # Сбор файлов по расширениям
        files_to_ingest = _collect_files(clone_dir, extensions)
        total = len(files_to_ingest)

        for file_path in files_to_ingest:
            rel_path = os.path.relpath(file_path, clone_dir).replace(os.sep, "/")
            try:
                mime = _guess_mime(rel_path)
                await upload_document(
                    session,
                    blob_store,
                    workspace_id=workspace_id,
                    corpus_id=corpus_id,
                    filename=rel_path,
                    mime=mime,
                    content=_read_file_async(file_path),
                    max_size_bytes=max_file_size_bytes,
                    allowed_extensions=extensions,
                    source_type="git",
                )
                ingested += 1
            except Exception as exc:  # noqa: BLE001 — граница системы: файлы из репозитория
                if _is_duplicate(exc):
                    skipped += 1
                else:
                    failed += 1
                    errors.append(f"{rel_path}: {exc}")
                    logger.warning("Failed to ingest %s from git repo: %s", rel_path, exc)

        await session.commit()
    finally:
        if clone_dir is not None:
            _cleanup_clone_dir(clone_dir)

    return GitIngestResult(
        total_files=total,
        ingested=ingested,
        skipped=skipped,
        failed=failed,
        errors=errors,
    )


async def _shallow_clone(
    repo_url: str,
    *,
    depth: int,
    timeout_seconds: int,
    max_clone_size_mb: int,
) -> str:
    """Выполняет shallow clone во временную директорию.

    Returns:
        Путь к директории с клоном.

    Raises:
        BadRequest: некорректный URL или clone не удался.
        OrqionError: таймаут или превышен лимит размера.
    """
    from dulwich import porcelain
    from dulwich.errors import GitProtocolError, NotGitRepository

    clone_dir = tempfile.mkdtemp(prefix="orqion-git-")

    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                porcelain.clone,
                repo_url,
                clone_dir,
                depth=depth if depth > 0 else None,
                checkout=True,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        _cleanup_clone_dir(clone_dir)
        raise OrqionError(
            f"Clone timed out after {timeout_seconds}s",
            constraint={"url": repo_url, "timeout_seconds": timeout_seconds},
            hint="Увеличьте --clone-timeout или проверьте доступность репозитория",
        )
    except (GitProtocolError, NotGitRepository, OSError, ValueError) as exc:
        _cleanup_clone_dir(clone_dir)
        raise BadRequest(
            f"Clone failed: {exc}",
            constraint={"url": repo_url},
            hint="Проверьте URL и доступность репозитория",
        )

    # Проверка размера клона (рабочее дерево + .git)
    clone_size = _dir_size(clone_dir)
    max_clone_size_bytes = max_clone_size_mb * 1024 * 1024
    if clone_size > max_clone_size_bytes:
        _cleanup_clone_dir(clone_dir)
        raise OrqionError(
            f"Clone size {clone_size // (1024 * 1024)} MB exceeds limit {max_clone_size_mb} MB",
            constraint={
                "url": repo_url,
                "clone_size_mb": clone_size // (1024 * 1024),
                "max_clone_size_mb": max_clone_size_mb,
            },
            hint="Увеличьте --max-clone-size или используйте более специфичный URL",
        )

    return clone_dir


def _collect_files(clone_dir: str, extensions: list[str]) -> list[str]:
    """Собирает файлы с подходящими расширениями, пропуская .git/."""
    extensions_lower = {ext.lower() for ext in extensions}
    result: list[str] = []

    for root, dirs, files in os.walk(clone_dir):
        if ".git" in dirs:
            dirs.remove(".git")
        for fn in files:
            if Path(fn).suffix.lower() in extensions_lower:
                result.append(os.path.join(root, fn))

    return sorted(result)


def _guess_mime(filename: str) -> str:
    """Угадывает MIME-тип по расширению файла."""
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def _dir_size(path: str) -> int:
    """Вычисляет суммарный размер всех файлов в директории (рекурсивно)."""
    total = 0
    for root, _, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return total


def _cleanup_clone_dir(clone_dir: str) -> None:
    """Удаляет временную директорию клона. Игнорирует ошибки (best-effort)."""
    try:
        shutil.rmtree(clone_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001 — cleanup, не должен прерывать flow
        logger.warning("Failed to cleanup clone dir %s", clone_dir)


def _read_file_async(file_path: str) -> AsyncIterator[bytes]:
    """Читает файл с диска и отдаёт чанками как AsyncIterator.

    Блокирующий open()/read() вынесен в thread через anyio, чтобы не
    блокировать event loop при чтении больших файлов.
    """
    import anyio

    async def _gen() -> AsyncIterator[bytes]:
        f = await anyio.to_thread.run_sync(open, file_path, "rb")
        try:
            while True:
                chunk = await anyio.to_thread.run_sync(f.read, _READ_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            await anyio.to_thread.run_sync(f.close)

    return _gen()


def _is_duplicate(exc: Exception) -> bool:
    """Проверяет, является ли ошибка DuplicateDocument."""
    from app.errors import DuplicateDocument

    return isinstance(exc, DuplicateDocument)
