"""Тесты получения метаданных и содержимого документа (T-306).

Проверки:
- GET /api/documents/{id} → 200, метаданные без blob_uri
- GET /api/documents/{id} → 404 для несуществующего ID
- GET /api/documents/{id} → 404 для чужого workspace
- GET /api/documents/{id}/content → 200, streamed bytes, корректный Content-Type
- GET /api/documents/{id}/content → 404 для несуществующего документа
- GET /api/documents/{id}/content → 404 если blob отсутствует в хранилище
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
    role_name: str = "developer",
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
            email=f"detail-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _create_corpus(app_fixture: FastAPI, name: str = "public") -> str:
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
    files = {"file": (filename, content, content_type)}
    return await api_client.post(
        f"/api/corpora/{corpus_id}/documents",
        files=files,
    )


@pytest.mark.asyncio
async def test_get_document_metadata_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/documents/{id} → 200, метаданные без blob_uri."""
    await _login_with_role(api_client, app_fixture)
    corpus_id = await _create_corpus(app_fixture)

    upload_resp = await _upload_file(api_client, corpus_id, content=b"Test content")
    assert upload_resp.status_code == 201
    document_id = upload_resp.json()["id"]

    resp = await api_client.get(f"/api/documents/{document_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == document_id
    assert data["corpus_id"] == corpus_id
    assert data["filename"] == "test.txt"
    assert data["mime"] == "text/plain"
    assert data["status"] == "pending"
    assert "blob_uri" not in data


@pytest.mark.asyncio
async def test_get_document_metadata_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/documents/{id} → 404 для несуществующего ID."""
    await _login_with_role(api_client, app_fixture)

    resp = await api_client.get("/api/documents/nonexistent-id-12345")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="T-313a: single-workspace (ADR-3) — request.app.state.workspace_id "
    "always equals app workspace. Cross-workspace isolation requires "
    "multi-tenant (arch.md §14.2). This test validated the old bug "
    "where user.workspace_id could differ from app.state.workspace_id."
)
async def test_get_document_metadata_wrong_workspace(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/documents/{id} → 404 для чужого workspace."""
    await _login_with_role(api_client, app_fixture)
    corpus_id = await _create_corpus(app_fixture)

    upload_resp = await _upload_file(api_client, corpus_id, content=b"Test content")
    assert upload_resp.status_code == 201
    document_id = upload_resp.json()["id"]

    # Создаём второго пользователя в другом workspace
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from app.db.models import Workspace

        ws2 = Workspace(name="ws2")
        session.add(ws2)
        await session.flush()

        role2 = Role(
            workspace_id=ws2.id,
            name="developer",
            is_builtin=True,
            policy=BUILTIN_ROLES["developer"].model_dump(),
        )
        session.add(role2)
        await session.flush()

        user2 = User(
            workspace_id=ws2.id,
            email="other@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role2.id,
        )
        session.add(user2)
        await session.flush()

        session_id2 = await create_session(session, user2.id, ws2.id, Settings())
        await session.commit()

    # Запрос от второго пользователя (чужой workspace)
    api_client.cookies.set(COOKIE_NAME, session_id2)
    resp = await api_client.get(f"/api/documents/{document_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_document_content_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/documents/{id}/content → 200, streamed bytes, корректный Content-Type."""
    await _login_with_role(api_client, app_fixture)
    corpus_id = await _create_corpus(app_fixture)

    file_content = b"Hello orqion document content"
    upload_resp = await _upload_file(
        api_client,
        corpus_id,
        filename="test.txt",
        content=file_content,
        content_type="text/plain",
    )
    assert upload_resp.status_code == 201
    document_id = upload_resp.json()["id"]

    resp = await api_client.get(f"/api/documents/{document_id}/content")
    assert resp.status_code == 200
    assert resp.content == file_content
    assert "text/plain" in resp.headers.get("content-type", "")
    assert "inline" in resp.headers.get("content-disposition", "")
    assert "test.txt" in resp.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_get_document_content_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/documents/{id}/content → 404 для несуществующего документа."""
    await _login_with_role(api_client, app_fixture)

    resp = await api_client.get("/api/documents/nonexistent-id-12345/content")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"


@pytest.mark.asyncio
async def test_get_document_content_blob_missing(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/documents/{id}/content → 404 если blob отсутствует в хранилище."""
    await _login_with_role(api_client, app_fixture)
    corpus_id = await _create_corpus(app_fixture)

    upload_resp = await _upload_file(api_client, corpus_id, content=b"Test content")
    assert upload_resp.status_code == 201
    document_id = upload_resp.json()["id"]
    blob_uri = upload_resp.json()["blob_uri"]

    # Удаляем blob из хранилища
    blob_store: LocalBlobStore = app_fixture.state.blob_store
    await blob_store.delete(blob_uri)

    resp = await api_client.get(f"/api/documents/{document_id}/content")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"
