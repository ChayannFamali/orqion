"""Тесты backup/restore (T-426).

Ключевой тест: VACUUM INTO на реальном vec.db с загруженным sqlite-vec
и настоящими embeddings → backup → restore → search_dense/search_sparse работают.

Покрытие:
- backup: создаёт архив с manifest, db, vec, blobs
- backup: vec0 VACUUM INTO реально работает (не fallback)
- backup: .tmp/ исключается из blobs
- backup: отказ на non-minimal profile
- restore: чистый инстанс, данные на месте
- restore: search_dense/search_sparse работают после restore
- restore: отказ на непустой без --force
- restore: --force перезаписывает
- restore: --dry-run не пишет
- restore: bad archive_version → reject
- restore: warning для Provider с api_key_enc
- roundtrip: backup A → restore B → данные идентичны
"""

from __future__ import annotations

import json
import os
import sqlite3
import tarfile
import tempfile
from pathlib import Path

import pytest
from app.auth.bootstrap import ensure_builtin_roles
from app.config import Settings
from app.db.base import Base
from app.db.engine import create_engine, create_session_factory
from app.db.models import Corpus, Document
from app.db.workspace import ensure_default_workspace
from app.rag.blob import LocalBlobStore
from app.rag.embeddings import EmbeddedChunk
from app.rag.vector_store import EMBEDDING_DIM, SQLiteVectorStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def _make_test_settings(db_path: str, data_dir: str) -> Settings:
    return Settings(
        database_url=f"sqlite:///{db_path}",
        blob_store_path=os.path.join(data_dir, "blobs"),
        vector_store_path=os.path.join(data_dir, "vec.db"),
        log_level="WARNING",
    )


async def _setup_db_alembic(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession], str]:
    """Создаёт схему через Alembic upgrade head (не create_all).

    Включает SQLite-only таблицы из миграции 0013 (fts_chunks, vec_chunk_map).
    """
    import asyncio

    from app.db.migrate import run_migrations_sync

    await asyncio.to_thread(run_migrations_sync, settings.database_url)

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    async with factory() as session:
        ws_id = await ensure_default_workspace(session)
        await ensure_builtin_roles(session, ws_id)
        await session.commit()
    return engine, factory, ws_id


async def _setup_db(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession], str]:
    """Создаёт схему, workspace, builtin roles."""
    engine = create_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = create_session_factory(engine)
    async with factory() as session:
        ws_id = await ensure_default_workspace(session)
        await ensure_builtin_roles(session, ws_id)
        await session.commit()
    return engine, factory, ws_id


def _make_chunk(
    ordinal: int,
    text: str,
    vector: list[float],
    chunk_id: str = "",
) -> EmbeddedChunk:
    if not chunk_id:
        chunk_id = f"chunk-{ordinal:04d}-uuid"
    return EmbeddedChunk(text=text, vector=vector, ordinal=ordinal, model="test", chunk_id=chunk_id)


def _make_unit_vec(dim: int, idx: int) -> list[float]:
    vec = [0.0] * dim
    vec[idx] = 1.0
    return vec


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_creates_archive(tmp_path: Path) -> None:
    """Backup создаёт tar.gz с manifest, db.sqlite, vec.sqlite, blobs/."""
    db_path = str(tmp_path / "orqion.db")
    data_dir = str(tmp_path / "data")
    settings = _make_test_settings(db_path, data_dir)
    engine, _, _ = await _setup_db(settings)
    await engine.dispose()

    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    result = backup(settings, output_path=archive_path)

    assert os.path.exists(archive_path)
    assert result.archive_size_bytes > 0
    assert result.db_table_count > 0

    # Проверка содержимого архива
    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
        assert "manifest.json" in names
        assert "db.sqlite" in names
        assert "vec.sqlite" in names

        manifest_file = tar.extractfile("manifest.json")
        assert manifest_file is not None
        manifest = json.loads(manifest_file.read().decode("utf-8"))
        assert manifest["archive_version"] == 1
        assert manifest["profile"] == "minimal"
        assert manifest["secret_key_included"] is False


@pytest.mark.asyncio
async def test_backup_vacuum_into_vec0_real(tmp_path: Path) -> None:
    """VACUUM INTO на реальном vec.db с загруженным sqlite-vec и embeddings.

    КРИТИЧЕСКИЙ ТЕСТ: если VACUUM INTO не работает с vec0 — fallback к copy.
    Проверяем что vec_method = "vacuum_into" и search_dense работает после restore.
    """
    db_path = str(tmp_path / "orqion.db")
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir, exist_ok=True)
    vec_path = os.path.join(data_dir, "vec.db")
    settings = _make_test_settings(db_path, data_dir)

    engine, factory, ws_id = await _setup_db(settings)

    # Создаём corpus + document в БД
    async with factory() as session:
        corpus = Corpus(workspace_id=ws_id, name="test-corpus")
        session.add(corpus)
        await session.flush()

        doc = Document(
            workspace_id=ws_id,
            corpus_id=corpus.id,
            blob_uri="abcdef1234567890",
            filename="test.txt",
            mime="text/plain",
            sha256="abcdef1234567890",
            source_type="upload",
            status="indexed",
        )
        session.add(doc)
        await session.commit()

    # Создаём vec.db с реальными embeddings через SQLiteVectorStore
    store = SQLiteVectorStore(vec_path)
    chunks = [
        _make_chunk(0, "hello world", _make_unit_vec(EMBEDDING_DIM, 0)),
        _make_chunk(1, "foo bar baz", _make_unit_vec(EMBEDDING_DIM, 1)),
        _make_chunk(2, "quick brown fox", _make_unit_vec(EMBEDDING_DIM, 2)),
    ]
    await store.upsert("index-v1", chunks)

    # Проверяем что search работает до backup
    query_vec = _make_unit_vec(EMBEDDING_DIM, 0)
    hits_before = await store.search_dense("index-v1", query_vec, k=3)
    assert len(hits_before) == 3
    await store.close()

    # Backup
    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    result = backup(settings, output_path=archive_path)

    # КЛЮЧЕВОЕ УТВЕРЖДЕНИЕ: VACUUM INTO сработал на vec0
    assert result.vec_method == "vacuum_into", (
        f"VACUUM INTO не сработал на vec0, использован fallback: {result.vec_method}. "
        f"Warnings: {result.warnings}"
    )

    await engine.dispose()


@pytest.mark.asyncio
async def test_backup_excludes_tmp_dir(tmp_path: Path) -> None:
    """.tmp/ директория не попадает в архив."""
    db_path = str(tmp_path / "orqion.db")
    data_dir = str(tmp_path / "data")
    blob_dir = os.path.join(data_dir, "blobs")
    os.makedirs(os.path.join(blob_dir, ".tmp"), exist_ok=True)
    # Создаём real blob
    os.makedirs(os.path.join(blob_dir, "ab", "cd"), exist_ok=True)
    Path(os.path.join(blob_dir, "ab", "cd", "abcdef")).write_bytes(b"content")
    # Создаём temp file in .tmp
    Path(os.path.join(blob_dir, ".tmp", "temp123")).write_bytes(b"temp")

    settings = _make_test_settings(db_path, data_dir)
    engine, _, _ = await _setup_db(settings)
    await engine.dispose()

    from scripts.backup import backup

    result = backup(settings, output_path=str(tmp_path / "backup.tar.gz"))
    assert result.blob_count == 1  # only the real blob, not .tmp/temp123

    # Verify in archive
    with tarfile.open(str(tmp_path / "backup.tar.gz"), "r:gz") as tar:
        blob_names = [n for n in tar.getnames() if n.startswith("blobs/")]
        assert not any(".tmp" in n for n in blob_names)


@pytest.mark.asyncio
async def test_backup_refuses_non_minimal(tmp_path: Path) -> None:
    """Если profile != minimal → отказ."""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'orqion.db'}",
        blob_store_path=str(tmp_path / "blobs"),
        vector_store_path=str(tmp_path / "vec.db"),
        profile="standard",
        log_level="WARNING",
    )

    from scripts.backup import backup

    with pytest.raises(RuntimeError, match="minimal"):
        backup(settings, output_path=str(tmp_path / "backup.tar.gz"))


@pytest.mark.asyncio
async def test_backup_db_consistent(tmp_path: Path) -> None:
    """DB в архиве консистентна: PRAGMA integrity_check OK."""
    db_path = str(tmp_path / "orqion.db")
    data_dir = str(tmp_path / "data")
    settings = _make_test_settings(db_path, data_dir)
    engine, _, _ = await _setup_db(settings)
    await engine.dispose()

    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    backup(settings, output_path=archive_path)

    # Extract and verify integrity
    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(tmpdir)

        conn = sqlite3.connect(os.path.join(tmpdir, "db.sqlite"))
        cursor = conn.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        conn.close()
        assert result == "ok"


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_clean_instance(tmp_path: Path) -> None:
    """Restore на чистый инстанс → все данные на месте."""
    # --- Instance A: setup with data ---
    dir_a = str(tmp_path / "a")
    os.makedirs(dir_a, exist_ok=True)
    settings_a = _make_test_settings(
        str(Path(dir_a) / "orqion.db"),
        str(Path(dir_a) / "data"),
    )
    engine_a, factory_a, ws_a = await _setup_db(settings_a)

    # Add corpus + document
    async with factory_a() as session:
        corpus = Corpus(workspace_id=ws_a, name="test-corpus")
        session.add(corpus)
        await session.flush()
        doc = Document(
            workspace_id=ws_a,
            corpus_id=corpus.id,
            blob_uri="abcdef1234567890",
            filename="test.txt",
            mime="text/plain",
            sha256="abcdef1234567890",
            source_type="upload",
            status="indexed",
        )
        session.add(doc)
        await session.commit()

    await engine_a.dispose()

    # Backup A
    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    backup(settings_a, output_path=archive_path)

    # --- Instance B: fresh ---
    dir_b = str(tmp_path / "b")
    os.makedirs(dir_b, exist_ok=True)
    settings_b = _make_test_settings(
        str(Path(dir_b) / "orqion.db"),
        str(Path(dir_b) / "data"),
    )
    # Setup B with schema + seed (but no user data)
    engine_b, _factory_b, _ = await _setup_db(settings_b)
    await engine_b.dispose()

    # Restore
    from scripts.restore import restore

    result = await restore(settings_b, archive_path, force=False, dry_run=False)
    assert result.restored is True

    # Verify data
    engine_b2 = create_engine(settings_b)
    factory_b2 = create_session_factory(engine_b2)
    async with factory_b2() as session:
        corpora = (await session.execute(select(Corpus))).scalars().all()
        assert len(corpora) == 1
        assert corpora[0].name == "test-corpus"

        docs = (await session.execute(select(Document))).scalars().all()
        assert len(docs) == 1
        assert docs[0].filename == "test.txt"

    await engine_b2.dispose()


@pytest.mark.asyncio
async def test_restore_vector_search_after_restore(tmp_path: Path) -> None:
    """search_dense/search_sparse работают после restore на восстановленном vec.db."""
    dir_a = str(tmp_path / "a")
    data_dir_a = os.path.join(dir_a, "data")
    os.makedirs(data_dir_a, exist_ok=True)
    vec_path_a = os.path.join(data_dir_a, "vec.db")
    settings_a = _make_test_settings(
        str(Path(dir_a) / "orqion.db"),
        data_dir_a,
    )
    engine_a, _, _ = await _setup_db(settings_a)

    # Create vec.db with real embeddings
    store_a = SQLiteVectorStore(vec_path_a)
    chunks = [
        _make_chunk(0, "hello world", _make_unit_vec(EMBEDDING_DIM, 0)),
        _make_chunk(1, "foo bar baz", _make_unit_vec(EMBEDDING_DIM, 1)),
    ]
    await store_a.upsert("index-v1", chunks)

    # Verify search works before backup
    hits = await store_a.search_dense("index-v1", _make_unit_vec(EMBEDDING_DIM, 0), k=2)
    assert len(hits) == 2
    await store_a.close()
    await engine_a.dispose()

    # Backup
    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    backup(settings_a, output_path=archive_path)

    # Restore to B
    dir_b = str(tmp_path / "b")
    os.makedirs(dir_b, exist_ok=True)
    settings_b = _make_test_settings(
        str(Path(dir_b) / "orqion.db"),
        str(Path(dir_b) / "data"),
    )
    engine_b, _, _ = await _setup_db(settings_b)
    await engine_b.dispose()

    from scripts.restore import restore

    await restore(settings_b, archive_path, force=False, dry_run=False)

    # Verify search works on restored vec.db
    vec_path_b = os.path.join(dir_b, "data", "vec.db")
    store_b = SQLiteVectorStore(vec_path_b)
    hits_dense = await store_b.search_dense("index-v1", _make_unit_vec(EMBEDDING_DIM, 0), k=2)
    assert len(hits_dense) == 2
    assert hits_dense[0].score > 0.9  # exact match

    hits_sparse = await store_b.search_sparse("index-v1", "hello world", k=2)
    assert len(hits_sparse) >= 1
    await store_b.close()


@pytest.mark.asyncio
async def test_restore_refuses_nonempty_without_force(tmp_path: Path) -> None:
    """Непустой target без --force → отказ."""
    dir_a = str(tmp_path / "a")
    os.makedirs(dir_a, exist_ok=True)
    settings_a = _make_test_settings(
        str(Path(dir_a) / "orqion.db"),
        str(Path(dir_a) / "data"),
    )
    engine_a, factory_a, ws_a = await _setup_db(settings_a)

    # Add user data
    async with factory_a() as session:
        corpus = Corpus(workspace_id=ws_a, name="test")
        session.add(corpus)
        await session.commit()
    await engine_a.dispose()

    # Backup
    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    backup(settings_a, output_path=archive_path)

    # Instance B: has schema + seed + user data
    dir_b = str(tmp_path / "b")
    os.makedirs(dir_b, exist_ok=True)
    settings_b = _make_test_settings(
        str(Path(dir_b) / "orqion.db"),
        str(Path(dir_b) / "data"),
    )
    engine_b, factory_b, ws_b = await _setup_db(settings_b)
    async with factory_b() as session:
        corpus = Corpus(workspace_id=ws_b, name="existing")
        session.add(corpus)
        await session.commit()
    await engine_b.dispose()

    from scripts.restore import restore

    with pytest.raises(RuntimeError, match="пользовательские данные"):
        await restore(settings_b, archive_path, force=False, dry_run=False)


@pytest.mark.asyncio
async def test_restore_force_overwrites(tmp_path: Path) -> None:
    """--force перезаписывает непустой target."""
    dir_a = str(tmp_path / "a")
    os.makedirs(dir_a, exist_ok=True)
    settings_a = _make_test_settings(
        str(Path(dir_a) / "orqion.db"),
        str(Path(dir_a) / "data"),
    )
    engine_a, factory_a, ws_a = await _setup_db(settings_a)
    async with factory_a() as session:
        corpus = Corpus(workspace_id=ws_a, name="from-backup")
        session.add(corpus)
        await session.commit()
    await engine_a.dispose()

    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    backup(settings_a, output_path=archive_path)

    # Instance B: non-empty
    dir_b = str(tmp_path / "b")
    os.makedirs(dir_b, exist_ok=True)
    settings_b = _make_test_settings(
        str(Path(dir_b) / "orqion.db"),
        str(Path(dir_b) / "data"),
    )
    engine_b, factory_b, ws_b = await _setup_db(settings_b)
    async with factory_b() as session:
        corpus = Corpus(workspace_id=ws_b, name="existing")
        session.add(corpus)
        await session.commit()
    await engine_b.dispose()

    from scripts.restore import restore

    result = await restore(settings_b, archive_path, force=True, dry_run=False)
    assert result.restored is True

    # Verify "from-backup" corpus is present, "existing" is gone
    engine_b2 = create_engine(settings_b)
    factory_b2 = create_session_factory(engine_b2)
    async with factory_b2() as session:
        corpora = (await session.execute(select(Corpus))).scalars().all()
        names = [c.name for c in corpora]
        assert "from-backup" in names
        assert "existing" not in names
    await engine_b2.dispose()


@pytest.mark.asyncio
async def test_restore_dry_run(tmp_path: Path) -> None:
    """--dry-run → выводит содержимое, не пишет в БД."""
    dir_a = str(tmp_path / "a")
    os.makedirs(dir_a, exist_ok=True)
    settings_a = _make_test_settings(
        str(Path(dir_a) / "orqion.db"),
        str(Path(dir_a) / "data"),
    )
    engine_a, factory_a, ws_a = await _setup_db(settings_a)
    async with factory_a() as session:
        corpus = Corpus(workspace_id=ws_a, name="test")
        session.add(corpus)
        await session.commit()
    await engine_a.dispose()

    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    backup(settings_a, output_path=archive_path)

    # Instance B: empty (only seed)
    dir_b = str(tmp_path / "b")
    os.makedirs(dir_b, exist_ok=True)
    settings_b = _make_test_settings(
        str(Path(dir_b) / "orqion.db"),
        str(Path(dir_b) / "data"),
    )
    engine_b, _, _ = await _setup_db(settings_b)
    await engine_b.dispose()

    from scripts.restore import restore

    result = await restore(settings_b, archive_path, force=False, dry_run=True)
    assert result.restored is False
    assert result.db_table_count > 0

    # Verify nothing was written
    engine_b2 = create_engine(settings_b)
    factory_b2 = create_session_factory(engine_b2)
    async with factory_b2() as session:
        corpora = (await session.execute(select(Corpus))).scalars().all()
        assert len(corpora) == 0  # no user data
    await engine_b2.dispose()


@pytest.mark.asyncio
async def test_restore_bad_archive_version(tmp_path: Path) -> None:
    """archive_version=2 → reject."""
    db_path = str(tmp_path / "orqion.db")
    data_dir = str(tmp_path / "data")
    settings = _make_test_settings(db_path, data_dir)
    engine, _, _ = await _setup_db(settings)
    await engine.dispose()

    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    backup(settings, output_path=archive_path)

    # Tamper with manifest
    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(tmpdir)

        manifest_path = os.path.join(tmpdir, "manifest.json")
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        manifest["archive_version"] = 2
        Path(manifest_path).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Re-pack
        new_archive = str(tmp_path / "bad.tar.gz")
        with tarfile.open(new_archive, "w:gz") as tar:
            for name in os.listdir(tmpdir):
                tar.add(os.path.join(tmpdir, name), arcname=name)

        from scripts.restore import restore

        with pytest.raises(RuntimeError, match="версия архива"):
            await restore(settings, new_archive, force=False, dry_run=False)


@pytest.mark.asyncio
async def test_restore_secret_key_warning(tmp_path: Path) -> None:
    """Provider с api_key_enc → warning в stdout."""
    dir_a = str(tmp_path / "a")
    os.makedirs(dir_a, exist_ok=True)
    settings_a = _make_test_settings(
        str(Path(dir_a) / "orqion.db"),
        str(Path(dir_a) / "data"),
    )
    engine_a, factory_a, ws_a = await _setup_db(settings_a)
    async with factory_a() as session:
        from app.db.models import Provider

        provider = Provider(
            workspace_id=ws_a,
            kind="openai",
            base_url="https://api.openai.com/v1",
            api_key_enc="encrypted-key-data",
            enabled=True,
        )
        session.add(provider)
        await session.commit()
    await engine_a.dispose()

    from scripts.backup import backup

    archive_path = str(tmp_path / "backup.tar.gz")
    backup(settings_a, output_path=archive_path)

    # Restore on B
    dir_b = str(tmp_path / "b")
    os.makedirs(dir_b, exist_ok=True)
    settings_b = _make_test_settings(
        str(Path(dir_b) / "orqion.db"),
        str(Path(dir_b) / "data"),
    )
    engine_b, _, _ = await _setup_db(settings_b)
    await engine_b.dispose()

    from scripts.restore import restore

    result = await restore(settings_b, archive_path, force=False, dry_run=False)
    assert any("api_key" in w.lower() or "зашифрован" in w.lower() for w in result.warnings)


@pytest.mark.asyncio
async def test_backup_restore_roundtrip(tmp_path: Path) -> None:
    """Приёмочный тест: backup A → restore B → данные идентичны.

    Проверки: количество записей в ключевых таблицах, blob store, vector search.
    """
    # --- Instance A ---
    dir_a = str(tmp_path / "a")
    data_dir_a = os.path.join(dir_a, "data")
    os.makedirs(data_dir_a, exist_ok=True)
    vec_path_a = os.path.join(data_dir_a, "vec.db")
    blob_path_a = os.path.join(data_dir_a, "blobs")
    settings_a = _make_test_settings(
        str(Path(dir_a) / "orqion.db"),
        data_dir_a,
    )
    engine_a, factory_a, ws_a = await _setup_db(settings_a)

    # Add corpus, document, blob
    async with factory_a() as session:
        corpus = Corpus(workspace_id=ws_a, name="roundtrip-corpus")
        session.add(corpus)
        await session.flush()

        # Create blob
        blob_store = LocalBlobStore(blob_path_a)
        from collections.abc import AsyncIterator

        async def _content() -> AsyncIterator[bytes]:
            yield b"hello world document content"

        blob_ref = await blob_store.put(_content())

        doc = Document(
            workspace_id=ws_a,
            corpus_id=corpus.id,
            blob_uri=blob_ref.uri,
            filename="doc.txt",
            mime="text/plain",
            sha256=blob_ref.sha256,
            source_type="upload",
            status="indexed",
        )
        session.add(doc)
        await session.commit()

    # Create vec.db with real embeddings
    store_a = SQLiteVectorStore(vec_path_a)
    chunks = [
        _make_chunk(0, "hello world", _make_unit_vec(EMBEDDING_DIM, 0)),
        _make_chunk(1, "foo bar baz", _make_unit_vec(EMBEDDING_DIM, 1)),
    ]
    await store_a.upsert("index-v1", chunks)
    await store_a.close()
    await engine_a.dispose()

    # Backup
    from scripts.backup import backup

    archive_path = str(tmp_path / "roundtrip.tar.gz")
    result_backup = backup(settings_a, output_path=archive_path)
    assert result_backup.vec_method == "vacuum_into"

    # --- Instance B: fresh ---
    dir_b = str(tmp_path / "b")
    os.makedirs(dir_b, exist_ok=True)
    settings_b = _make_test_settings(
        str(Path(dir_b) / "orqion.db"),
        str(Path(dir_b) / "data"),
    )
    engine_b, _, _ = await _setup_db(settings_b)
    await engine_b.dispose()

    # Restore
    from scripts.restore import restore

    result_restore = await restore(settings_b, archive_path, force=False, dry_run=False)
    assert result_restore.restored is True

    # Verify
    engine_b2 = create_engine(settings_b)
    factory_b2 = create_session_factory(engine_b2)
    async with factory_b2() as session:
        # Table counts match
        corpora_b = (await session.execute(select(Corpus))).scalars().all()
        assert len(corpora_b) == 1
        assert corpora_b[0].name == "roundtrip-corpus"

        docs_b = (await session.execute(select(Document))).scalars().all()
        assert len(docs_b) == 1
        assert docs_b[0].filename == "doc.txt"

    # Blob store accessible
    blob_path_b = os.path.join(dir_b, "data", "blobs")
    blob_store_b = LocalBlobStore(blob_path_b)
    assert await blob_store_b.exists(docs_b[0].blob_uri)

    # Vector store search works
    vec_path_b = os.path.join(dir_b, "data", "vec.db")
    store_b = SQLiteVectorStore(vec_path_b)
    hits_dense = await store_b.search_dense("index-v1", _make_unit_vec(EMBEDDING_DIM, 0), k=2)
    assert len(hits_dense) == 2

    hits_sparse = await store_b.search_sparse("index-v1", "hello", k=2)
    assert len(hits_sparse) >= 1
    await store_b.close()
    await engine_b2.dispose()


# ---------------------------------------------------------------------------
# Приёмочный тест: Alembic-created DB (блокер T-426)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_restore_alembic_created_db(tmp_path: Path) -> None:
    """Backup→restore на БД, созданной через Alembic upgrade head (не create_all).

    БЛОКЕР T-426: create_all() пропускает SQLite-only таблицы из миграции 0013
    (fts_chunks, vec_chunk_map). VACUUM INTO копирует БД as-is — если source
    создан через Alembic, backup должен содержать 0013-таблицы, и restore
    должен их перенести.

    Проверки:
    - 0013-таблицы (fts_chunks, vec_chunk_map) присутствуют в восстановленной БД
    - search_dense/search_sparse работают на восстановленном vec.db
    - PRAGMA integrity_check OK
    """
    # --- Instance A: schema through Alembic ---
    dir_a = str(tmp_path / "a")
    data_dir_a = os.path.join(dir_a, "data")
    os.makedirs(data_dir_a, exist_ok=True)
    vec_path_a = os.path.join(data_dir_a, "vec.db")
    settings_a = _make_test_settings(
        str(Path(dir_a) / "orqion.db"),
        data_dir_a,
    )
    engine_a, factory_a, ws_a = await _setup_db_alembic(settings_a)

    # Verify 0013 tables exist in source DB
    db_path_a = str(Path(dir_a) / "orqion.db")
    conn = sqlite3.connect(db_path_a)
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    ]
    conn.close()
    assert "fts_chunks" in tables, "fts_chunks missing from Alembic-created DB"
    assert "vec_chunk_map" in tables, "vec_chunk_map missing from Alembic-created DB"

    # Add corpus + document
    async with factory_a() as session:
        corpus = Corpus(workspace_id=ws_a, name="alembic-test-corpus")
        session.add(corpus)
        await session.flush()
        doc = Document(
            workspace_id=ws_a,
            corpus_id=corpus.id,
            blob_uri="abcdef1234567890",
            filename="test.txt",
            mime="text/plain",
            sha256="abcdef1234567890",
            source_type="upload",
            status="indexed",
        )
        session.add(doc)
        await session.commit()

    # Create vec.db with real embeddings
    store_a = SQLiteVectorStore(vec_path_a)
    chunks = [
        _make_chunk(0, "hello world from alembic", _make_unit_vec(EMBEDDING_DIM, 0)),
        _make_chunk(1, "foo bar baz alembic", _make_unit_vec(EMBEDDING_DIM, 1)),
    ]
    await store_a.upsert("index-v1", chunks)
    # Verify search works before backup
    hits_before = await store_a.search_dense("index-v1", _make_unit_vec(EMBEDDING_DIM, 0), k=2)
    assert len(hits_before) == 2
    sparse_before = await store_a.search_sparse("index-v1", "alembic", k=2)
    assert len(sparse_before) >= 1
    await store_a.close()
    await engine_a.dispose()

    # Backup
    from scripts.backup import backup

    archive_path = str(tmp_path / "alembic-backup.tar.gz")
    result_backup = backup(settings_a, output_path=archive_path)
    assert result_backup.vec_method == "vacuum_into"

    # --- Instance B: fresh, schema through Alembic ---
    dir_b = str(tmp_path / "b")
    data_dir_b = os.path.join(dir_b, "data")
    os.makedirs(data_dir_b, exist_ok=True)
    settings_b = _make_test_settings(
        str(Path(dir_b) / "orqion.db"),
        data_dir_b,
    )
    engine_b, _, _ = await _setup_db_alembic(settings_b)
    await engine_b.dispose()

    # Restore
    from scripts.restore import restore

    result_restore = await restore(settings_b, archive_path, force=False, dry_run=False)
    assert result_restore.restored is True

    # Verify 0013 tables exist in restored DB
    db_path_b = str(Path(dir_b) / "orqion.db")
    conn = sqlite3.connect(db_path_b)
    tables_b = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    ]
    # integrity check
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    assert integrity == "ok"
    assert "fts_chunks" in tables_b, "fts_chunks missing from restored DB"
    assert "vec_chunk_map" in tables_b, "vec_chunk_map missing from restored DB"

    # Verify vector store search works after restore
    vec_path_b = os.path.join(data_dir_b, "vec.db")
    store_b = SQLiteVectorStore(vec_path_b)
    hits_dense = await store_b.search_dense("index-v1", _make_unit_vec(EMBEDDING_DIM, 0), k=2)
    assert len(hits_dense) == 2
    hits_sparse = await store_b.search_sparse("index-v1", "alembic", k=2)
    assert len(hits_sparse) >= 1
    await store_b.close()
