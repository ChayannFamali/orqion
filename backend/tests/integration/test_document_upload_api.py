"""Тесты загрузки документов (T-204).

Проверки:
- успешная загрузка → 201, blob существует, document status=pending
- дубликат по sha256 → 409
- превышение размера → 413
- неразрешённое расширение → 415
- корпус не найден → 404
- роль support (нет capability upload) → 403
- список документов корпуса
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Corpus, Role, User
from app.policy.presets import BUILTIN_ROLES
from app.rag.blob import LocalBlobStore
from fastapi import FastAPI


async def _login_with_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str = "admin",
    policy: dict[str, Any] | None = None,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role_policy = policy or BUILTIN_ROLES[role_name].model_dump()
        role = Role(
            workspace_id=workspace_id,
            name=role_name,
            is_builtin=True,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email=f"doc-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _create_corpus(app_fixture: FastAPI, name: str = "Test corpus") -> str:
    """Создаёт корпус и возвращает его ID."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(name=name, workspace_id=workspace_id)
        session.add(corpus)
        await session.flush()
        corpus_id = corpus.id
        await session.commit()
    return corpus_id


async def _upload_file(
    api_client: httpx.AsyncClient,
    corpus_id: str,
    *,
    filename: str = "test.txt",
    content: bytes = b"Hello orqion",
    content_type: str = "text/plain",
) -> httpx.Response:
    """Загружает файл через POST multipart."""
    files = {"file": (filename, content, content_type)}
    return await api_client.post(
        f"/api/corpora/{corpus_id}/documents",
        files=files,
    )


@pytest.mark.asyncio
async def test_upload_document_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Успешная загрузка → 201, blob существует, document status=pending."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    resp = await _upload_file(api_client, corpus_id, content=b"Test content")

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert data["filename"] == "test.txt"
    assert data["corpus_id"] == corpus_id
    assert data["sha256"]
    assert data["blob_uri"]
    assert data["source_type"] == "upload"

    # Blob существует в blob store
    blob_store: LocalBlobStore = app_fixture.state.blob_store
    assert await blob_store.exists(data["blob_uri"])


@pytest.mark.asyncio
async def test_upload_duplicate_detected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Дубликат по sha256 → 409 с указанием document_id."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    resp1 = await _upload_file(api_client, corpus_id, content=b"Same content")
    assert resp1.status_code == 201

    resp2 = await _upload_file(api_client, corpus_id, content=b"Same content")
    assert resp2.status_code == 409
    data = resp2.json()
    assert data["error"] == "duplicate_document"
    assert "sha256" in data["constraint"]
    assert "document_id" in data["constraint"]


@pytest.mark.asyncio
async def test_upload_file_too_large(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Превышение размера → 413."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    # Создаём файл больше дефолтного лимита (50 МБ)
    # Используем 51 МБ — но это 51 МБ в памяти.
    # Вместо этого — переопределим settings через патч.
    # Проще: отправим 60 байт и проверим логику через маленький лимит.
    # Логика проверки: total_size > max_size_bytes.
    # Проверим с реальным лимитом 50 МБ — нужен большой файл.
    # Альтернатива: проверяем, что малый файл проходит, а превышение
    # тестируем на уровне service.
    # Для интеграционного теста используем 60 МБ — слишком много для CI.
    # Проверим через unit-тест service-слоя с малым лимитом.
    # Здесь — только что малый файл проходит (уже test_upload_document_success).
    # Для интеграционного теста: создадим файл чуть больше 50 МБ.
    # 50 МБ = 52428800 байт. Создадим 52428801 байт.
    # Это ~50 МБ в памяти — приемлемо для одного теста.
    large_content = b"x" * (50 * 1024 * 1024 + 1)
    resp = await _upload_file(api_client, corpus_id, content=large_content)

    assert resp.status_code == 413
    data = resp.json()
    assert data["error"] == "file_too_large"
    assert data["constraint"]["max_size_bytes"] == 50 * 1024 * 1024


@pytest.mark.asyncio
async def test_upload_file_type_not_allowed(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Неразрешённое расширение → 415."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    resp = await _upload_file(
        api_client,
        corpus_id,
        filename="malware.exe",
        content=b"MZ\x90\x00",
        content_type="application/x-msdownload",
    )

    assert resp.status_code == 415
    data = resp.json()
    assert data["error"] == "file_type_not_allowed"
    assert ".exe" not in data["constraint"]["allowed_extensions"]


@pytest.mark.asyncio
async def test_upload_corpus_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Корпус не найден → 404."""
    await _login_with_role(api_client, app_fixture, role_name="developer")

    resp = await _upload_file(
        api_client,
        "nonexistent-corpus-id",
        content=b"test",
    )

    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"


@pytest.mark.asyncio
async def test_upload_permission_denied(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Роль support (нет capability upload) → 403."""
    await _login_with_role(api_client, app_fixture, role_name="support")
    corpus_id = await _create_corpus(app_fixture)

    resp = await _upload_file(api_client, corpus_id, content=b"test")

    assert resp.status_code == 403
    data = resp.json()
    assert data["error"] == "upload_permission_denied"


@pytest.mark.asyncio
async def test_list_documents(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET возвращает документы корпуса."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    # Загружаем два файла
    resp1 = await _upload_file(api_client, corpus_id, filename="a.txt", content=b"content A")
    assert resp1.status_code == 201
    resp2 = await _upload_file(api_client, corpus_id, filename="b.md", content=b"content B")
    assert resp2.status_code == 201

    # GET list
    resp = await api_client.get(f"/api/corpora/{corpus_id}/documents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    filenames = {d["filename"] for d in data["documents"]}
    assert filenames == {"a.txt", "b.md"}
    for doc in data["documents"]:
        assert doc["status"] == "pending"


@pytest.mark.asyncio
async def test_same_content_different_corpus_allowed(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Тот же файл в другой корпус — легитимная отдельная запись (не дубликат)."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus1 = await _create_corpus(app_fixture, "Project A")
    corpus2 = await _create_corpus(app_fixture, "Project B")

    shared_content = b"Shared README content"
    resp1 = await _upload_file(api_client, corpus1, filename="readme.md", content=shared_content)
    assert resp1.status_code == 201

    resp2 = await _upload_file(api_client, corpus2, filename="readme.md", content=shared_content)
    assert resp2.status_code == 201
    assert resp1.json()["id"] != resp2.json()["id"]
    assert resp1.json()["sha256"] == resp2.json()["sha256"]
    assert resp1.json()["blob_uri"] == resp2.json()["blob_uri"]
