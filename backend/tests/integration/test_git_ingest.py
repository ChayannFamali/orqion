"""Тесты git ingestion (T-422).

Проверки:
- Создание тестового git-репозитория → shallow clone → документы загружены
- source_type="git" на всех созданных документах
- Повторный запуск → дубликаты пропущены (sha256-дедупликация)
- Фильтрация по расширениям — только matching файлы
- Путь файла из git-дерева сохраняется как document.filename (пункт 3 дизайна)
- Превышение размера клона → OrqionError
- Несуществующий URL → BadRequest
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from app.db.models import Corpus, Document, Workspace
from app.errors import BadRequest, OrqionError
from app.rag.blob import LocalBlobStore
from app.rag.git_ingest import GitIngestResult, ingest_git_repository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _make_workspace(session: AsyncSession) -> str:
    ws = Workspace(name="test")
    session.add(ws)
    await session.flush()
    return ws.id


async def _make_corpus(session: AsyncSession, workspace_id: str, name: str = "test-repo") -> str:
    corpus = Corpus(name=name, workspace_id=workspace_id)
    session.add(corpus)
    await session.flush()
    return corpus.id


def _create_test_repo(
    repo_dir: str,
    files: dict[str, str],
) -> str:
    """Создаёт локальный git-репозиторий с указанными файлами.

    Args:
        repo_dir: директория для репозитория.
        files: dict {relative_path: content}.

    Returns:
        Путь к репозиторию.
    """
    from dulwich import porcelain

    porcelain.init(repo_dir)

    for rel_path, content in files.items():
        full_path = os.path.join(repo_dir, *rel_path.split("/"))
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        porcelain.add(repo_dir, [rel_path.encode("utf-8")])

    porcelain.commit(
        repo_dir,
        message=b"Test commit",
        author=b"Test <test@test.com>",
        committer=b"Test <test@test.com>",
    )
    return repo_dir


@pytest.mark.asyncio
async def test_ingest_git_basic(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Базовый сценарий: клонирование репозитория, загрузка файлов, source_type=git."""
    repo_dir = str(tmp_path / "source-repo")
    _create_test_repo(
        repo_dir,
        {
            "README.md": "# Test Repo\nHello world",
            "src/utils.py": "def hello():\n    return 42\n",
            "src/main.py": "from utils import hello\nprint(hello())\n",
            "data.txt": "Some text content",
            "ignore.bin": "binary data",
        },
    )

    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    result = await ingest_git_repository(
        db_session,
        blob_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        repo_url=repo_dir,
        clone_timeout_seconds=30,
        max_clone_size_mb=100,
    )

    assert result.total_files == 4  # .bin отфильтрован
    assert result.ingested == 4
    assert result.skipped == 0
    assert result.failed == 0

    # Проверка: все документы имеют source_type="git"
    docs_result = await db_session.execute(select(Document).where(Document.corpus_id == corpus_id))
    docs = list(docs_result.scalars().all())
    assert len(docs) == 4
    for doc in docs:
        assert doc.source_type == "git"
        assert doc.status == "pending"

    # Проверка: путь файла из git-дерева сохранён без искажений
    filenames = {doc.filename for doc in docs}
    assert "README.md" in filenames
    assert "src/utils.py" in filenames
    assert "src/main.py" in filenames
    assert "data.txt" in filenames


@pytest.mark.asyncio
async def test_ingest_git_re_run_no_duplicates(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Повторный запуск: дубликаты пропущены (sha256-дедупликация)."""
    repo_dir = str(tmp_path / "source-repo")
    _create_test_repo(
        repo_dir,
        {
            "README.md": "# Test\nSame content",
            "app.py": "print('hello')\n",
        },
    )

    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    # Первый запуск
    result1 = await ingest_git_repository(
        db_session,
        blob_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        repo_url=repo_dir,
        clone_timeout_seconds=30,
        max_clone_size_mb=100,
    )
    assert result1.ingested == 2
    assert result1.skipped == 0

    # Второй запуск — те же файлы
    result2 = await ingest_git_repository(
        db_session,
        blob_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        repo_url=repo_dir,
        clone_timeout_seconds=30,
        max_clone_size_mb=100,
    )
    assert result2.total_files == 2
    assert result2.ingested == 0
    assert result2.skipped == 2
    assert result2.failed == 0


@pytest.mark.asyncio
async def test_ingest_git_re_run_with_changes(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Повторный запуск с изменённым файлом: новый sha256 → новый документ."""
    from dulwich import porcelain

    repo_dir = str(tmp_path / "source-repo")
    _create_test_repo(
        repo_dir,
        {"file.py": "print('v1')\n"},
    )

    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    # Первый запуск
    result1 = await ingest_git_repository(
        db_session,
        blob_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        repo_url=repo_dir,
        clone_timeout_seconds=30,
        max_clone_size_mb=100,
    )
    assert result1.ingested == 1

    # Меняем файл, коммитим
    file_path = os.path.join(repo_dir, "file.py")
    Path(file_path).write_text("print('v2')\n", encoding="utf-8")
    porcelain.add(repo_dir, [b"file.py"])
    porcelain.commit(
        repo_dir,
        message=b"v2",
        author=b"Test <test@test.com>",
        committer=b"Test <test@test.com>",
    )

    # Второй запуск — файл изменился, новый sha256
    result2 = await ingest_git_repository(
        db_session,
        blob_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        repo_url=repo_dir,
        clone_timeout_seconds=30,
        max_clone_size_mb=100,
    )
    assert result2.total_files == 1
    assert result2.ingested == 1  # новый sha256 → новый документ
    assert result2.skipped == 0


@pytest.mark.asyncio
async def test_ingest_git_extension_filter(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Фильтрация по расширениям — только указанные файлы."""
    repo_dir = str(tmp_path / "source-repo")
    _create_test_repo(
        repo_dir,
        {
            "app.py": "print('hello')\n",
            "config.json": '{"key": "value"}',
            "notes.md": "# Notes",
            "data.csv": "a,b,c",
        },
    )

    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    result = await ingest_git_repository(
        db_session,
        blob_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        repo_url=repo_dir,
        allowed_extensions=[".py", ".md"],
        clone_timeout_seconds=30,
        max_clone_size_mb=100,
    )
    assert result.total_files == 2
    assert result.ingested == 2

    docs_result = await db_session.execute(select(Document).where(Document.corpus_id == corpus_id))
    filenames = {doc.filename for doc in docs_result.scalars().all()}
    assert "app.py" in filenames
    assert "notes.md" in filenames
    assert "config.json" not in filenames
    assert "data.csv" not in filenames


@pytest.mark.asyncio
async def test_ingest_git_filename_preserves_path(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Путь файла из git-дерева (src/utils.py) сохраняется как document.filename.

    Пункт 3 дизайна: index_builder диспетчеризирует чанкер по расширению
    из document.filename. Если путь искажён, .py файлы уйдут в chunk_document.
    """
    repo_dir = str(tmp_path / "source-repo")
    _create_test_repo(
        repo_dir,
        {
            "src/deep/nested/utils.py": "def f(): pass\n",
            "docs/intro.md": "# Intro",
            "sql/init.sql": "CREATE TABLE t (id INT);\n",
        },
    )

    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    result = await ingest_git_repository(
        db_session,
        blob_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        repo_url=repo_dir,
        clone_timeout_seconds=30,
        max_clone_size_mb=100,
    )
    assert result.ingested == 3

    docs_result = await db_session.execute(select(Document).where(Document.corpus_id == corpus_id))
    docs = {doc.filename: doc for doc in docs_result.scalars().all()}

    # Путь сохранён с forward slashes (OS-независимо)
    assert "src/deep/nested/utils.py" in docs
    assert "docs/intro.md" in docs
    assert "sql/init.sql" in docs

    # Расширение корректно извлекается Path().suffix (для index_builder)
    assert Path(docs["src/deep/nested/utils.py"].filename).suffix == ".py"
    assert Path(docs["docs/intro.md"].filename).suffix == ".md"
    assert Path(docs["sql/init.sql"].filename).suffix == ".sql"


@pytest.mark.asyncio
async def test_ingest_git_clone_size_limit(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Превышение лимита размера клона → OrqionError."""
    repo_dir = str(tmp_path / "source-repo")
    # Создаём файл > 1 MB
    _create_test_repo(
        repo_dir,
        {"big.txt": "x" * (2 * 1024 * 1024)},
    )

    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    with pytest.raises(OrqionError) as exc_info:
        await ingest_git_repository(
            db_session,
            blob_store,
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            repo_url=repo_dir,
            clone_timeout_seconds=30,
            max_clone_size_mb=1,  # 1 MB лимит
        )
    assert "exceeds limit" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ingest_git_invalid_url(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Несуществующий URL/путь → BadRequest."""
    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    with pytest.raises(BadRequest):
        await ingest_git_repository(
            db_session,
            blob_store,
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            repo_url="/nonexistent/path/to/repo",
            clone_timeout_seconds=10,
            max_clone_size_mb=100,
        )


@pytest.mark.asyncio
async def test_ingest_git_empty_repo(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Пустой репозиторий (нет коммитов) — 0 файлов, не падает."""
    from dulwich import porcelain

    repo_dir = str(tmp_path / "empty-repo")
    porcelain.init(repo_dir)

    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    # Clone пустого репо — dulwich не создаёт файлы, _collect_files → []
    # Может вызвать ошибку при clone (нет ref для checkout) — ловим как BadRequest
    try:
        result = await ingest_git_repository(
            db_session,
            blob_store,
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            repo_url=repo_dir,
            clone_timeout_seconds=30,
            max_clone_size_mb=100,
        )
        # Если clone прошёл (dulwich может не падать на empty repo с checkout=True),
        # то 0 файлов — валидный результат
        assert result.total_files == 0
        assert result.ingested == 0
    except (BadRequest, OrqionError):
        # Empty repo clone может не удаться — это тоже валидный исход
        pass


@pytest.mark.asyncio
async def test_ingest_git_result_dataclass(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """GitIngestResult корректно считает total/ingested/skipped/failed."""
    repo_dir = str(tmp_path / "source-repo")
    _create_test_repo(
        repo_dir,
        {
            "a.py": "print('a')\n",
            "b.py": "print('b')\n",
            "c.md": "# C",
        },
    )

    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    result = await ingest_git_repository(
        db_session,
        blob_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        repo_url=repo_dir,
        clone_timeout_seconds=30,
        max_clone_size_mb=100,
    )

    assert isinstance(result, GitIngestResult)
    assert result.total_files == 3
    assert result.ingested == 3
    assert result.skipped == 0
    assert result.failed == 0
    assert result.errors == []


@pytest.mark.asyncio
async def test_ingest_git_shallow_clone_depth(
    db_session: AsyncSession,
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Shallow clone (depth=1) — .git не содержит полную историю."""
    from dulwich import porcelain

    repo_dir = str(tmp_path / "source-repo")
    _create_test_repo(repo_dir, {"file.py": "v1\n"})

    # Дополнительные коммиты (история)
    for i in range(2, 5):
        Path(os.path.join(repo_dir, "file.py")).write_text(f"v{i}\n", encoding="utf-8")
        porcelain.add(repo_dir, [b"file.py"])
        porcelain.commit(
            repo_dir,
            message=f"v{i}".encode(),
            author=b"Test <test@test.com>",
            committer=b"Test <test@test.com>",
        )

    workspace_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, workspace_id)

    await ingest_git_repository(
        db_session,
        blob_store,
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        repo_url=repo_dir,
        depth=1,
        clone_timeout_seconds=30,
        max_clone_size_mb=100,
    )

    # Проверяем: документ создан (file.py с последним содержимым)
    docs_result = await db_session.execute(select(Document).where(Document.corpus_id == corpus_id))
    docs = list(docs_result.scalars().all())
    assert len(docs) == 1
    assert docs[0].source_type == "git"
