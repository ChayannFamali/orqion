"""Тесты загрузки документов (T-204) и access control (T-313).

Проверки:
- успешная загрузка → 201, blob существует, document status=pending
- дубликат по sha256 → 409
- превышение размера → 413
- неразрешённое расширение → 415
- корпус не найден → 404
- роль support (нет capability upload) → 404 (скрываем существование)
- список документов корпуса
- policy.corpora visibility: corpus не в списке → 404
- manage_corpora без upload → list доступен, upload → 404
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


async def _create_corpus(
    app_fixture: FastAPI,
    name: str = "public",
    data_class: str | None = None,
) -> str:
    """Создаёт корпус и возвращает его ID."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(name=name, workspace_id=workspace_id, data_class=data_class)
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

    content = b"Test content"
    resp = await _upload_file(api_client, corpus_id, content=content)

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert data["filename"] == "test.txt"
    assert data["corpus_id"] == corpus_id
    assert data["sha256"]
    assert data["blob_uri"]
    assert data["source_type"] == "upload"
    # Реальный размер файла в байтах (закрытие «0 B» в списке документов)
    assert data["size_bytes"] == len(content)

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
    """Роль support (нет capability upload) → 404 (скрываем существование)."""
    await _login_with_role(api_client, app_fixture, role_name="support")
    corpus_id = await _create_corpus(app_fixture)

    resp = await _upload_file(api_client, corpus_id, content=b"test")

    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"


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
    corpus1 = await _create_corpus(app_fixture, "public")
    corpus2 = await _create_corpus(app_fixture, "team")

    shared_content = b"Shared README content"
    resp1 = await _upload_file(api_client, corpus1, filename="readme.md", content=shared_content)
    assert resp1.status_code == 201

    resp2 = await _upload_file(api_client, corpus2, filename="readme.md", content=shared_content)
    assert resp2.status_code == 201
    assert resp1.json()["id"] != resp2.json()["id"]
    assert resp1.json()["sha256"] == resp2.json()["sha256"]
    assert resp1.json()["blob_uri"] == resp2.json()["blob_uri"]


# ---------------------------------------------------------------------------
# Access control tests (T-313)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_denied_for_support_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Support (нет upload/manage_corpora) → 404 на list."""
    await _login_with_role(api_client, app_fixture, role_name="support")
    corpus_id = await _create_corpus(app_fixture)

    resp = await api_client.get(f"/api/corpora/{corpus_id}/documents")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_allowed_for_manage_corpora_without_upload(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Architect (manage_corpora, upload) → list доступен."""
    await _login_with_role(api_client, app_fixture, role_name="architect")
    corpus_id = await _create_corpus(app_fixture)

    resp = await api_client.get(f"/api/corpora/{corpus_id}/documents")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_upload_denied_for_manage_corpora_without_upload(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Custom role: manage_corpora but no upload → list OK, upload 404."""
    custom_policy = {
        "models": ["*"],
        "max_input_tokens": 100000,
        "max_output_tokens": 10000,
        "reasoning": "off",
        "budget": None,
        "rpm": 100,
        "tpm": 100000,
        "corpora": ["*"],
        "capabilities": ["manage_corpora", "chat"],
    }
    await _login_with_role(api_client, app_fixture, role_name="custom_mgr", policy=custom_policy)
    corpus_id = await _create_corpus(app_fixture)

    # List — доступен (manage_corpora)
    resp_list = await api_client.get(f"/api/corpora/{corpus_id}/documents")
    assert resp_list.status_code == 200

    # Upload — 404 (нет upload capability)
    resp_upload = await _upload_file(api_client, corpus_id, content=b"test")
    assert resp_upload.status_code == 404


@pytest.mark.asyncio
async def test_corpus_visibility_denied(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer с corpora=['public'] не видит корпус 'private'."""
    restricted_policy = {
        "models": ["*"],
        "max_input_tokens": 100000,
        "max_output_tokens": 10000,
        "reasoning": "off",
        "budget": None,
        "rpm": 100,
        "tpm": 100000,
        "corpora": ["public"],
        "capabilities": ["chat", "upload", "custom_prompts"],
    }
    await _login_with_role(
        api_client, app_fixture, role_name="restricted_dev", policy=restricted_policy
    )
    corpus_id = await _create_corpus(app_fixture, name="private-project")

    # Upload → 404 (corpus не в policy.corpora)
    resp_upload = await _upload_file(api_client, corpus_id, content=b"test")
    assert resp_upload.status_code == 404

    # List → 404
    resp_list = await api_client.get(f"/api/corpora/{corpus_id}/documents")
    assert resp_list.status_code == 404


@pytest.mark.asyncio
async def test_corpus_visibility_wildcard_allows_all(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin (corpora=['*']) видит любой корпус."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture, name="private-project")

    resp = await _upload_file(api_client, corpus_id, content=b"test")
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_document_detail_denied_without_capability(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Support (нет upload/manage_corpora) → 404 на GET /api/documents/{id}."""
    # Сначала загружаем как developer
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)
    upload_resp = await _upload_file(api_client, corpus_id, content=b"Test content")
    assert upload_resp.status_code == 201
    document_id = upload_resp.json()["id"]

    # Логинимся как support
    await _login_with_role(api_client, app_fixture, role_name="support")

    resp = await api_client.get(f"/api/documents/{document_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_document_content_denied_without_capability(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Support → 404 на GET /api/documents/{id}/content."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)
    upload_resp = await _upload_file(api_client, corpus_id, content=b"Test content")
    document_id = upload_resp.json()["id"]

    await _login_with_role(api_client, app_fixture, role_name="support")

    resp = await api_client.get(f"/api/documents/{document_id}/content")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_document_content_cyrillic_filename(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """BUG-021: не-ASCII имя файла не ломает отдачу /content (раньше 500)."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    cyrillic_name = "СОБЕСЕДОВАНИЕ - 2026-08-15_10-58-34.md"
    content = "# Заголовок интервью".encode()
    upload_resp = await _upload_file(
        api_client, corpus_id, filename=cyrillic_name, content=content, content_type="text/markdown"
    )
    assert upload_resp.status_code == 201, upload_resp.text
    assert upload_resp.json()["filename"] == cyrillic_name
    document_id = upload_resp.json()["id"]

    resp = await api_client.get(f"/api/documents/{document_id}/content")
    assert resp.status_code == 200, resp.text
    assert resp.content == content

    disposition = resp.headers.get("content-disposition", "")
    assert "filename*=UTF-8''" in disposition
    # Заголовок обязан быть латинице-безопасным (корень бага — 500 на отправке)
    disposition.encode("latin-1")


@pytest.mark.asyncio
async def test_document_detail_denied_for_invisible_corpus(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer с corpora=['public'] → 404 на документ в корпусе 'private'."""
    # Загружаем как admin (corpora=['*'])
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture, name="private-data")
    upload_resp = await _upload_file(api_client, corpus_id, content=b"Secret")
    document_id = upload_resp.json()["id"]

    # Логинимся как restricted developer
    restricted_policy = {
        "models": ["*"],
        "max_input_tokens": 100000,
        "max_output_tokens": 10000,
        "reasoning": "off",
        "budget": None,
        "rpm": 100,
        "tpm": 100000,
        "corpora": ["public"],
        "capabilities": ["chat", "upload", "custom_prompts"],
    }
    await _login_with_role(
        api_client, app_fixture, role_name="restricted_dev2", policy=restricted_policy
    )

    resp = await api_client.get(f"/api/documents/{document_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_document_content_denied_for_invisible_corpus(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer с corpora=['public'] → 404 на content в корпусе 'private'."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture, name="private-data")
    upload_resp = await _upload_file(api_client, corpus_id, content=b"Secret")
    document_id = upload_resp.json()["id"]

    restricted_policy = {
        "models": ["*"],
        "max_input_tokens": 100000,
        "max_output_tokens": 10000,
        "reasoning": "off",
        "budget": None,
        "rpm": 100,
        "tpm": 100000,
        "corpora": ["public"],
        "capabilities": ["chat", "upload", "custom_prompts"],
    }
    await _login_with_role(
        api_client, app_fixture, role_name="restricted_dev3", policy=restricted_policy
    )

    resp = await api_client.get(f"/api/documents/{document_id}/content")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE endpoint tests (T-313)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_document_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """DELETE /api/documents/{id} → 204 для документа без чанков."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    upload_resp = await _upload_file(api_client, corpus_id, content=b"Delete me")
    assert upload_resp.status_code == 201
    document_id = upload_resp.json()["id"]

    resp = await api_client.delete(f"/api/documents/{document_id}")
    assert resp.status_code == 204

    # Документ больше не доступен
    resp_get = await api_client.get(f"/api/documents/{document_id}")
    assert resp_get.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """DELETE /api/documents/{id} → 404 для несуществующего документа."""
    await _login_with_role(api_client, app_fixture, role_name="developer")

    resp = await api_client.delete("/api/documents/nonexistent-id-12345")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_denied_without_capability(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Support → 404 на DELETE."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)
    upload_resp = await _upload_file(api_client, corpus_id, content=b"Test")
    document_id = upload_resp.json()["id"]

    await _login_with_role(api_client, app_fixture, role_name="support")

    resp = await api_client.delete(f"/api/documents/{document_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_denied_for_invisible_corpus(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer с corpora=['public'] → 404 на DELETE документа в корпусе 'private'."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture, name="private-data")
    upload_resp = await _upload_file(api_client, corpus_id, content=b"Secret")
    document_id = upload_resp.json()["id"]

    restricted_policy = {
        "models": ["*"],
        "max_input_tokens": 100000,
        "max_output_tokens": 10000,
        "reasoning": "off",
        "budget": None,
        "rpm": 100,
        "tpm": 100000,
        "corpora": ["public"],
        "capabilities": ["chat", "upload", "custom_prompts"],
    }
    await _login_with_role(
        api_client, app_fixture, role_name="restricted_del", policy=restricted_policy
    )

    resp = await api_client.delete(f"/api/documents/{document_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_blocked_when_has_chunks(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """DELETE → 400 если документ имеет чанки в любой версии индекса."""
    from app.db.models import Chunk, IndexVersion

    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    upload_resp = await _upload_file(api_client, corpus_id, content=b"Indexed doc")
    assert upload_resp.status_code == 201
    document_id = upload_resp.json()["id"]

    # Создаём index_version + chunk вручную
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        iv = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            embedding_model="test",
            chunker="mixed-v1",
            chunker_version="1.0",
            status="active",
            stats={"status": "completed"},
        )
        session.add(iv)
        await session.flush()

        chunk = Chunk(
            workspace_id=workspace_id,
            index_version_id=iv.id,
            document_id=document_id,
            ordinal=0,
            text="test chunk",
            meta={},
        )
        session.add(chunk)
        await session.commit()

    resp = await api_client.delete(f"/api/documents/{document_id}")
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "bad_request"


# ---------------------------------------------------------------------------
# T-406a: Physical blob cleanup — reference counting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_document_removes_blob_when_last_ref(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """T-406a: удаление последнего документа, ссылающегося на blob → файл удалён."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    upload_resp = await _upload_file(api_client, corpus_id, content=b"unique blob content")
    assert upload_resp.status_code == 201
    document_id = upload_resp.json()["id"]

    # Blob существует
    factory = app_fixture.state.db_session_factory
    blob_store = app_fixture.state.blob_store
    async with factory() as session:
        from app.db.models import Document

        doc = await session.get(Document, document_id)
        assert doc is not None
        blob_uri = doc.blob_uri
        assert await blob_store.exists(blob_uri)

    # Удаляем документ
    resp = await api_client.delete(f"/api/documents/{document_id}")
    assert resp.status_code == 204

    # Blob удалён — последний документ удалён
    assert not await blob_store.exists(blob_uri)


@pytest.mark.asyncio
async def test_delete_document_keeps_blob_when_other_refs(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """T-406a: удаление документа при наличии других ссылок на тот же blob → файл остаётся.

    Два документа в разных корпусах с одинаковым содержимым (sha256) —
    dedup в BlobStore.put не дублирует файл. Удаление одного документа
    не должно удалять blob, пока второй документ ссылается на него.
    """
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id_1 = await _create_corpus(app_fixture, name="corpus-a")
    corpus_id_2 = await _create_corpus(app_fixture, name="corpus-b")

    # Загружаем один и тот же контент в два разных корпуса
    shared_content = b"shared blob content for dedup"
    upload1 = await _upload_file(api_client, corpus_id_1, content=shared_content)
    assert upload1.status_code == 201
    doc1_id = upload1.json()["id"]

    upload2 = await _upload_file(api_client, corpus_id_2, content=shared_content)
    assert upload2.status_code == 201
    doc2_id = upload2.json()["id"]

    # Оба документа ссылаются на один blob
    factory = app_fixture.state.db_session_factory
    blob_store = app_fixture.state.blob_store
    async with factory() as session:
        from app.db.models import Document

        doc1 = await session.get(Document, doc1_id)
        doc2 = await session.get(Document, doc2_id)
        assert doc1 is not None
        assert doc2 is not None
        assert doc1.blob_uri == doc2.blob_uri
        blob_uri = doc1.blob_uri
        assert await blob_store.exists(blob_uri)

    # Удаляем первый документ
    resp = await api_client.delete(f"/api/documents/{doc1_id}")
    assert resp.status_code == 204

    # Blob всё ещё существует — второй документ ссылается
    assert await blob_store.exists(blob_uri)

    # Удаляем второй документ
    resp = await api_client.delete(f"/api/documents/{doc2_id}")
    assert resp.status_code == 204

    # Теперь blob удалён — последний документ удалён
    assert not await blob_store.exists(blob_uri)


@pytest.mark.asyncio
async def test_concurrent_upload_with_duplicate_content(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """T-423: 3 параллельных upload, 2 с одинаковым sha256 — ни один не 500.

    file_a.py (content "AAA"), file_b.py (content "AAA"), file_c.py (content "BBB").
    Ожидаемый результат:
    - file_a.py: 201 (success)
    - file_b.py: 409 (duplicate_document) — независимо от того, кто выиграл race
    - file_c.py: 201 (success)
    - Ни один запрос не возвращает 500
    - В БД ровно 2 документа (один с "AAA", один с "BBB")
    """
    import asyncio

    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture, name="concurrent-test")

    content_a = b"AAA" * 100
    content_b = b"AAA" * 100  # same sha256 as content_a
    content_c = b"BBB" * 100

    # Запускаем 3 параллельных upload
    results = await asyncio.gather(
        _upload_file(api_client, corpus_id, filename="file_a.py", content=content_a),
        _upload_file(api_client, corpus_id, filename="file_b.py", content=content_b),
        _upload_file(api_client, corpus_id, filename="file_c.py", content=content_c),
        return_exceptions=True,
    )

    # Ни один не должен быть исключением (500)
    responses: list[httpx.Response] = [r for r in results if isinstance(r, httpx.Response)]
    assert len(responses) == 3, "Some uploads raised exceptions instead of HTTP responses"

    status_codes = sorted(r.status_code for r in responses)
    # Должно быть: 201, 201, 409
    assert status_codes == [201, 201, 409], f"Unexpected status codes: {status_codes}"

    # Ни один не должен быть 500
    for r in responses:
        assert r.status_code != 500, f"Server error on upload: {r.status_code}"

    # Проверяем БД: ровно 2 документа
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from app.db.models import Document
        from sqlalchemy import select

        docs_result = await session.execute(select(Document).where(Document.corpus_id == corpus_id))
        docs = list(docs_result.scalars().all())
        assert len(docs) == 2, f"Expected 2 documents, got {len(docs)}"

        # Один с sha256 от "AAA", один от "BBB"
        import hashlib

        sha_a = hashlib.sha256(content_a).hexdigest()
        sha_c = hashlib.sha256(content_c).hexdigest()
        sha_values = {doc.sha256 for doc in docs}
        assert sha_a in sha_values
        assert sha_c in sha_values
