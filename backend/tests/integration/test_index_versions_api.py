"""Тесты API управления версиями индекса (T-314).

Проверки:
- POST build → 202, index_version created with status=completed
- GET list → версии корпуса
- GET detail → прогресс версии
- POST activate → 200, warning при отсутствии eval run
- POST activate building version → 400
- POST rollback → 200
- POST cleanup → 200, deleted_count
- Access control: developer (no manage_corpora) → 404
- Corpus not found → 404
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Corpus, EvalRun, IndexVersion, Role, User
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
            email=f"iv-{role_name}@orqion.local",
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
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(name=name, workspace_id=workspace_id)
        session.add(corpus)
        await session.flush()
        corpus_id = corpus.id
        await session.commit()
    return corpus_id


@pytest.mark.asyncio
async def test_build_index_version(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/corpora/{id}/index-versions → 202, version created."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "completed"
    assert data["index_version_id"]

    # Version exists in DB with correct status
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        iv = await session.get(IndexVersion, data["index_version_id"])
        assert iv is not None
        assert iv.status == "completed"
        assert iv.corpus_id == corpus_id


@pytest.mark.asyncio
async def test_list_index_versions(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/corpora/{id}/index-versions → list of versions."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    # Build two versions
    resp1 = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    assert resp1.status_code == 202
    resp2 = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    assert resp2.status_code == 202

    resp = await api_client.get(f"/api/corpora/{corpus_id}/index-versions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(v["status"] == "completed" for v in data["versions"])


@pytest.mark.asyncio
async def test_get_index_version_detail(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/corpora/{id}/index-versions/{vid} → version details."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    build_resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    version_id = build_resp.json()["index_version_id"]

    resp = await api_client.get(f"/api/corpora/{corpus_id}/index-versions/{version_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == version_id
    assert data["corpus_id"] == corpus_id
    assert data["status"] == "completed"
    assert data["stats"] is not None


@pytest.mark.asyncio
async def test_activate_index_version(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST activate → 200, version becomes active."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    build_resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    version_id = build_resp.json()["index_version_id"]

    resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions/{version_id}/activate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_version_id"] == version_id
    assert data["previous_version_id"] is None
    # No eval run → warning
    assert data["warning"] is not None
    assert "оценки" in data["warning"]


@pytest.mark.asyncio
async def test_activate_already_active_version(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Повторная активация активной версии → 400 с понятным сообщением.

    Регресс: раньше повторный клик давал «версия не завершена, дождитесь
    сборки» — сообщение не про реальную причину (версия уже активна).
    """
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    build_resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    version_id = build_resp.json()["index_version_id"]

    first = await api_client.post(f"/api/corpora/{corpus_id}/index-versions/{version_id}/activate")
    assert first.status_code == 200

    second = await api_client.post(f"/api/corpora/{corpus_id}/index-versions/{version_id}/activate")
    assert second.status_code == 400
    assert "уже активна" in second.json()["hint"]


@pytest.mark.asyncio
async def test_activate_with_eval_run_no_warning(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST activate → 200, no warning when eval run exists."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    build_resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    version_id = build_resp.json()["index_version_id"]

    # Create an eval set + eval run manually
    from app.db.models import EvalSet

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        eval_set = EvalSet(
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            name="test-eval-set",
        )
        session.add(eval_set)
        await session.flush()

        eval_run = EvalRun(
            workspace_id=workspace_id,
            eval_set_id=eval_set.id,
            index_version_id=version_id,
            pipeline={},
            metrics={"recall@5": 0.8},
        )
        session.add(eval_run)
        await session.commit()

    resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions/{version_id}/activate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["warning"] is None


@pytest.mark.asyncio
async def test_rollback_index_version(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST rollback → 200, restores previous version."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    async def _post_expect(url: str, expect: int) -> dict[str, Any]:
        # Флакующий тест (однократный сбой 400 вместо 200, запись в
        # бэклоге планинга): при любом будущем падении тело ответа
        # эндпоинта фиксируется в сообщении автоматически — поимка не
        # зависит от того, кто и когда прогоняет. Сборка возвращает 202,
        # активация и откат — 200.
        r = await api_client.post(url)
        assert r.status_code == expect, f"{url} → {r.status_code}: {r.text[:500]}"
        body: dict[str, Any] = r.json()
        return body

    # Build and activate v1
    build1 = await _post_expect(f"/api/corpora/{corpus_id}/index-versions", 202)
    v1_id = build1["index_version_id"]
    await _post_expect(f"/api/corpora/{corpus_id}/index-versions/{v1_id}/activate", 200)

    # Build and activate v2
    build2 = await _post_expect(f"/api/corpora/{corpus_id}/index-versions", 202)
    v2_id = build2["index_version_id"]
    await _post_expect(f"/api/corpora/{corpus_id}/index-versions/{v2_id}/activate", 200)

    # Rollback
    data = await _post_expect(f"/api/corpora/{corpus_id}/index-versions/rollback", 200)
    assert data["active_version_id"] == v1_id, f"rollback вернул не ту версию: {data}"


@pytest.mark.asyncio
async def test_cleanup_retired_versions(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST cleanup → 200, deleted_count."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    # Build and activate v1
    build1 = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    v1_id = build1.json()["index_version_id"]
    await api_client.post(f"/api/corpora/{corpus_id}/index-versions/{v1_id}/activate")

    # Build and activate v2 (v1 → retired)
    build2 = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    v2_id = build2.json()["index_version_id"]
    await api_client.post(f"/api/corpora/{corpus_id}/index-versions/{v2_id}/activate")

    # Cleanup
    resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions/cleanup")
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted_count"] == 1


@pytest.mark.asyncio
async def test_cleanup_interrupted_versions(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """BUG-020: cleanup удаляет interrupted-версии (мусор прерванных сборок).

    Активная и completed версии остаются: interrupted никогда не была
    активна и ничего не защищает, а активная/завершённая — рабочие.
    """
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    # Активная версия (через build + activate)
    build = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    active_id = build.json()["index_version_id"]
    await api_client.post(f"/api/corpora/{corpus_id}/index-versions/{active_id}/activate")

    # Прерванные версии — прямой записью (эмуляция прерванной сборки)
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    interrupted_ids: list[str] = []
    async with factory() as session:
        for _ in range(2):
            iv = IndexVersion(
                workspace_id=workspace_id,
                corpus_id=corpus_id,
                embedding_model="test-embed",
                chunker="doc",
                chunker_version="v1",
                status="interrupted",
            )
            session.add(iv)
            await session.flush()
            interrupted_ids.append(iv.id)
        await session.commit()

    resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions/cleanup")
    assert resp.status_code == 200
    assert resp.json()["deleted_count"] == 2

    # interrupted удалены, активная осталась
    async with factory() as session:
        for iv_id in interrupted_ids:
            assert await session.get(IndexVersion, iv_id) is None
        active = await session.get(IndexVersion, active_id)
        assert active is not None
        assert active.status == "active"


@pytest.mark.asyncio
async def test_access_denied_for_developer(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer (no manage_corpora) → 404 on all endpoints."""
    await _login_with_role(api_client, app_fixture, role_name="developer")
    corpus_id = await _create_corpus(app_fixture)

    resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    assert resp.status_code == 404

    resp = await api_client.get(f"/api/corpora/{corpus_id}/index-versions")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_corpus_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Nonexistent corpus → 404."""
    await _login_with_role(api_client, app_fixture, role_name="admin")

    resp = await api_client.post("/api/corpora/nonexistent-id/index-versions")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_activate_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Activate nonexistent version → 404."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions/nonexistent-id/activate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rollback_no_previous(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Rollback without prior activate → 400."""
    await _login_with_role(api_client, app_fixture, role_name="admin")
    corpus_id = await _create_corpus(app_fixture)

    resp = await api_client.post(f"/api/corpora/{corpus_id}/index-versions/rollback")
    assert resp.status_code == 400
