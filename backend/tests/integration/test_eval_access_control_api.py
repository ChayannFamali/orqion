"""Тесты access control eval API и single-item CRUD (T-315).

Проверки:
- developer (no manage_corpora) → 404 на все eval endpoints
- architect (manage_corpora) → доступ
- POST /eval-sets/{id}/items — create single item → 201
- DELETE /eval-sets/{id}/items/{item_id} → 204
- DELETE nonexistent item → 404
- corpus not found → 404
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Corpus, EvalSet, Role, User
from app.policy.presets import BUILTIN_ROLES
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
            email=f"eval-{role_name}@orqion.local",
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


async def _create_eval_set(
    app_fixture: FastAPI,
    corpus_id: str,
    name: str = "Test eval set",
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        eval_set = EvalSet(
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            name=name,
        )
        session.add(eval_set)
        await session.flush()
        eval_set_id = eval_set.id
        await session.commit()
    return eval_set_id


# ---------------------------------------------------------------------------
# Access control tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_developer_denied_create_eval_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer (no manage_corpora) → 404 на create eval set."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={"name": "test", "items": []},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_developer_denied_list_eval_sets(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer → 404 на list eval sets."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    resp = await api_client.get(f"/api/corpora/{corpus_id}/eval-sets")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_developer_denied_compare(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer → 404 на compare."""
    await _login_with_role(api_client, app_fixture, role_name="developer")

    resp = await api_client.post(
        "/api/eval-runs/compare",
        json={"run_ids": ["id1", "id2"]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_architect_allowed_create_eval_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Architect (manage_corpora) → 201 на create eval set."""
    await _login_with_role(api_client, app_fixture, role_name="architect")
    corpus_id = await _create_corpus(app_fixture)

    resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={
            "name": "architect-eval",
            "items": [
                {
                    "question": "What is RAG?",
                    "expected_doc_ids": [],
                    "expected_answer": None,
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "architect-eval"
    assert len(data["items"]) == 1


# ---------------------------------------------------------------------------
# Single EvalItem CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_single_eval_item(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /eval-sets/{id}/items → 201."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)
    eval_set_id = await _create_eval_set(app_fixture, corpus_id)

    resp = await api_client.post(
        f"/api/eval-sets/{eval_set_id}/items",
        json={
            "question": "What is chunking?",
            "expected_doc_ids": [],
            "expected_answer": "Splitting text into pieces",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["question"] == "What is chunking?"
    assert data["eval_set_id"] == eval_set_id
    assert data["expected_answer"] == "Splitting text into pieces"


@pytest.mark.asyncio
async def test_delete_single_eval_item(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """DELETE /eval-sets/{id}/items/{item_id} → 204."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)
    eval_set_id = await _create_eval_set(app_fixture, corpus_id)

    # Create item
    create_resp = await api_client.post(
        f"/api/eval-sets/{eval_set_id}/items",
        json={"question": "Delete me", "expected_doc_ids": [], "expected_answer": None},
    )
    assert create_resp.status_code == 201
    item_id = create_resp.json()["id"]

    # Delete item
    resp = await api_client.delete(f"/api/eval-sets/{eval_set_id}/items/{item_id}")
    assert resp.status_code == 204

    # Verify item is gone
    get_resp = await api_client.get(f"/api/eval-sets/{eval_set_id}")
    assert get_resp.status_code == 200
    assert all(i["id"] != item_id for i in get_resp.json()["items"])


@pytest.mark.asyncio
async def test_delete_eval_item_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """DELETE nonexistent item → 404."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)
    eval_set_id = await _create_eval_set(app_fixture, corpus_id)

    resp = await api_client.delete(f"/api/eval-sets/{eval_set_id}/items/nonexistent-item-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_eval_item_eval_set_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST item to nonexistent eval set → 404."""
    await _login_with_role(api_client, app_fixture, role_name="admin")

    resp = await api_client.post(
        "/api/eval-sets/nonexistent-set-id/items",
        json={"question": "test", "expected_doc_ids": [], "expected_answer": None},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_developer_denied_create_eval_item(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer → 404 на create single item."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)
    eval_set_id = await _create_eval_set(app_fixture, corpus_id)

    resp = await api_client.post(
        f"/api/eval-sets/{eval_set_id}/items",
        json={"question": "test", "expected_doc_ids": [], "expected_answer": None},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_developer_denied_delete_eval_item(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer → 404 на delete single item."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)
    eval_set_id = await _create_eval_set(app_fixture, corpus_id)

    resp = await api_client.delete(f"/api/eval-sets/{eval_set_id}/items/nonexistent-item-id")
    assert resp.status_code == 404
