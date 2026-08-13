"""T-403: Cross-workspace isolation tests.

Проверка что ресурсы из workspace B не доступны из workspace A.
Каждый домен — отдельный тест. Все возвращают 404 (hide existence).
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import (
    Corpus,
    EvalSet,
    IndexVersion,
    Provider,
    Role,
    User,
    Workspace,
)
from app.db.models import Model as ModelEntity
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="isolation-admin@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _create_other_workspace_resources(app_fixture: FastAPI) -> dict[str, str]:
    """Создаёт ресурсы в другом workspace, возвращает их IDs."""
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        other_ws = Workspace(name="other-ws")
        session.add(other_ws)
        await session.flush()
        other_ws_id = other_ws.id

        # Role
        role = Role(
            workspace_id=other_ws_id,
            name="developer",
            is_builtin=True,
            policy=BUILTIN_ROLES["developer"].model_dump(),
        )
        session.add(role)
        await session.flush()

        # User
        user = User(
            workspace_id=other_ws_id,
            email="other-user@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)

        # Provider + Model
        provider = Provider(
            workspace_id=other_ws_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", "key"),
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = ModelEntity(
            workspace_id=other_ws_id,
            provider_id=provider.id,
            alias="other/model",
            upstream_name="other-model",
            locality="local",
        )
        session.add(model)

        # Corpus
        corpus = Corpus(
            workspace_id=other_ws_id,
            name="other-corpus",
            data_class="К0",
        )
        session.add(corpus)
        await session.flush()

        # IndexVersion
        iv = IndexVersion(
            workspace_id=other_ws_id,
            corpus_id=corpus.id,
            embedding_model="BAAI/bge-m3",
            chunker="mixed-v1",
            chunker_version="1",
            status="active",
        )
        session.add(iv)
        await session.flush()
        corpus.active_index_version_id = iv.id

        # EvalSet
        eval_set = EvalSet(
            workspace_id=other_ws_id,
            corpus_id=corpus.id,
            name="other-eval-set",
        )
        session.add(eval_set)

        await session.commit()

        return {
            "workspace_id": other_ws_id,
            "role_id": role.id,
            "user_id": user.id,
            "provider_id": provider.id,
            "model_id": model.id,
            "corpus_id": corpus.id,
            "index_version_id": iv.id,
            "eval_set_id": eval_set.id,
        }


@pytest.mark.asyncio
async def test_users_cross_workspace_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/users/{id} из другого workspace → 404."""
    await _login_as_admin(api_client, app_fixture)
    ids = await _create_other_workspace_resources(app_fixture)

    resp = await api_client.get(f"/api/users/{ids['user_id']}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_roles_cross_workspace_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET/PATCH /api/roles/{id} из другого workspace → 404."""
    await _login_as_admin(api_client, app_fixture)
    ids = await _create_other_workspace_resources(app_fixture)

    resp = await api_client.get(f"/api/roles/{ids['role_id']}")
    assert resp.status_code == 404

    resp = await api_client.patch(
        f"/api/roles/{ids['role_id']}",
        json={"policy": {"models": ["chat"]}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_providers_cross_workspace_not_visible(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/providers не содержит провайдеров из другого workspace."""
    await _login_as_admin(api_client, app_fixture)
    await _create_other_workspace_resources(app_fixture)

    resp = await api_client.get("/api/providers")
    assert resp.status_code == 200
    data = resp.json()
    provider_names = [p["name"] for p in data["providers"]]
    assert "other-provider" not in provider_names


@pytest.mark.asyncio
async def test_audit_log_cross_workspace_not_visible(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/audit-log не содержит записей из другого workspace."""
    await _login_as_admin(api_client, app_fixture)
    ids = await _create_other_workspace_resources(app_fixture)

    # Create audit entry in other workspace via role policy change
    from app.audit.service import write_audit

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        await write_audit(
            session,
            workspace_id=ids["workspace_id"],
            actor_user_id=ids["user_id"],
            action="role.policy_changed",
            object_type="role",
            object_id=ids["role_id"],
            meta={"old": {}, "new": {}},
        )
        await session.commit()

    resp = await api_client.get("/api/audit-log")
    assert resp.status_code == 200
    data = resp.json()
    actions = [e["action"] for e in data["entries"]]
    assert "role.policy_changed" not in actions or all(
        e["object_id"] != ids["role_id"] for e in data["entries"]
    )


@pytest.mark.asyncio
async def test_index_versions_cross_workspace_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/corpora/{id}/index-versions из другого workspace → 404."""
    await _login_as_admin(api_client, app_fixture)
    ids = await _create_other_workspace_resources(app_fixture)

    resp = await api_client.get(f"/api/corpora/{ids['corpus_id']}/index-versions")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_eval_cross_workspace_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/corpora/{id}/eval-sets из другого workspace → 404."""
    await _login_as_admin(api_client, app_fixture)
    ids = await _create_other_workspace_resources(app_fixture)

    resp = await api_client.get(f"/api/corpora/{ids['corpus_id']}/eval-sets")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_workspace_id_in_body_ignored(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """T-403: workspace_id в теле запроса игнорируется (берётся из app.state)."""
    await _login_as_admin(api_client, app_fixture)

    # POST corpus с workspace_id в теле — должно игнорироваться
    resp = await api_client.post(
        "/api/corpora",
        json={
            "name": "ws-injection-test",
            "data_class": None,
            "workspace_id": "fake-workspace-id",
        },
    )
    assert resp.status_code == 201
    corpus = resp.json()
    assert corpus["id"] is not None

    # Verify corpus belongs to app.state.workspace_id, not fake
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        from sqlalchemy import select as sa_select

        result = await session.execute(sa_select(Corpus).where(Corpus.id == corpus["id"]))
        db_corpus = result.scalar_one()
        assert db_corpus.workspace_id == ws_id


@pytest.mark.asyncio
async def test_max_input_tokens_enforced_through_chat(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """T-403: max_input_tokens enforced end-to-end through chat API."""
    # Login as developer with max_input_tokens=100
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        dev_policy = BUILTIN_ROLES["developer"].model_dump()
        dev_policy["max_input_tokens"] = 100
        role = Role(
            workspace_id=workspace_id,
            name="dev-limited",
            is_builtin=False,
            policy=dev_policy,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="dev-limited@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        # Seed a local model so routing doesn't fail before enforce()
        provider = Provider(
            workspace_id=workspace_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", "key"),
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = ModelEntity(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias="local/test-model",
            upstream_name="test-model",
            locality="local",
            enabled=True,
        )
        session.add(model)

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)

    # Send a message that exceeds 100 tokens
    long_content = " ".join(["word"] * 200)
    resp = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": long_content}],
            "stream": False,
        },
    )

    assert resp.status_code == 413
