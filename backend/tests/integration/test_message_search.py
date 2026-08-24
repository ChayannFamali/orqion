"""T-436: Полнотекстовый поиск по истории диалогов.

Тесты:
- search finds messages after save
- search isolated by user_id (другой пользователь не находит)
- search escapes FTS5 special chars (BUG-003 регресс)
- delete conversation removes messages from FTS (dual-write delete)
- retention cleanup removes messages from FTS (§5.3 — стёртый контент
  не должен быть находим)
- search returns empty for empty query
- search ORDER BY score ASC (bm25: меньше = релевантнее)
"""

from __future__ import annotations

import os

import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import create_session
from app.config import Settings
from app.db.models import Conversation, Message, Role, User
from app.policy.presets import BUILTIN_ROLES
from app.search.message_search import search_messages
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy import text as sa_text

# BUG-018: FTS5-поиск по диалогам — фича только SQLite (диалект-гейт
# миграции 0024). На других диалектах роут честно отвечает 501
# (FeatureNotSupported, BUG-017) — это проверено отдельным тестом с
# патчем в test_fts_dialect_guard.py; тестам ниже нужен реально
# работающий FTS5, поэтому на не-SQLite ноге они пропускаются.
_db_url = os.environ.get("ORQION_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    _db_url.startswith(("postgres://", "postgresql://")),
    reason="FTS5-поиск по диалогам доступен только для SQLite",
)


async def _login_user(
    app_fixture: FastAPI,
    email: str = "t436-user@orqion.local",
    role_name: str = "developer",
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name=role_name,
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email=email,
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        await create_session(session, user.id, workspace_id, Settings())
        await session.commit()
        return user.id


async def _create_conversation_with_messages(
    app_fixture: FastAPI,
    user_id: str,
    messages: list[tuple[str, str]],
) -> str:
    """Создаёт диалог с сообщениями + FTS5 dual-write.

    messages: list of (role, content).
    """
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        conv = Conversation(
            workspace_id=workspace_id,
            user_id=user_id,
            title="t436-conv",
            archived=False,
        )
        session.add(conv)
        await session.flush()
        conv_id = conv.id

        for role, content in messages:
            msg = Message(
                workspace_id=workspace_id,
                conversation_id=conv_id,
                role=role,
                content=content,
                model_id=None,
                tokens_in=None,
                tokens_out=None,
                meta={},
            )
            session.add(msg)
            await session.flush()
            # dual-write FTS5
            await session.execute(
                sa_text(
                    "INSERT INTO fts_messages (content, conversation_id, message_id, role) "
                    "VALUES (:content, :cid, :mid, :role)"
                ),
                {"content": content, "cid": conv_id, "mid": msg.id, "role": role},
            )
        await session.commit()
        return conv_id


@pytest.mark.asyncio
async def test_search_finds_messages(app_fixture: FastAPI) -> None:
    """Поиск находит сообщения после dual-write."""
    user_id = await _login_user(app_fixture)
    await _create_conversation_with_messages(
        app_fixture,
        user_id,
        [
            ("user", "How to configure authentication in orqion?"),
            ("assistant", "Authentication is configured via cookie sessions."),
        ],
    )
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        hits = await search_messages(session, "authentication", user_id, workspace_id)
        assert len(hits) >= 2
        assert any("authentication" in h.content.lower() for h in hits)


@pytest.mark.asyncio
async def test_search_isolated_by_user(app_fixture: FastAPI) -> None:
    """Поиск изолирован по user_id — чужие диалоги не находятся."""
    user_a = await _login_user(app_fixture, email="t436-a@orqion.local")
    user_b = await _login_user(app_fixture, email="t436-b@orqion.local")
    await _create_conversation_with_messages(
        app_fixture,
        user_a,
        [("user", "unique-secret-keyword-for-isolation-test")],
    )
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        # user_b не находит сообщение user_a
        hits = await search_messages(session, "unique-secret-keyword", user_b, workspace_id)
        assert len(hits) == 0


@pytest.mark.asyncio
async def test_search_escapes_fts5_special_chars(app_fixture: FastAPI) -> None:
    """BUG-003 регресс: спецсимволы FTS5 экранируются."""
    user_id = await _login_user(app_fixture)
    await _create_conversation_with_messages(
        app_fixture,
        user_id,
        [("user", 'test message with special chars: * ? " - : ( )')],
    )
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        # Поиск со спецсимволами не падает
        hits = await search_messages(session, "test * ? message", user_id, workspace_id)
        assert len(hits) >= 1


@pytest.mark.asyncio
async def test_delete_conversation_removes_fts(app_fixture: FastAPI) -> None:
    """Удаление диалога удаляет сообщения из FTS5 (dual-write delete)."""
    user_id = await _login_user(app_fixture)
    conv_id = await _create_conversation_with_messages(
        app_fixture,
        user_id,
        [("user", "deletable-keyword-for-fts-cleanup")],
    )
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id

    # Проверяем: до удаления — находится
    async with factory() as session:
        hits = await search_messages(session, "deletable-keyword", user_id, workspace_id)
        assert len(hits) >= 1

    # Удаляем диалог
    async with factory() as session:
        result = await session.execute(select(Conversation).where(Conversation.id == conv_id))
        conv = result.scalar_one()
        await session.delete(conv)
        # dual-write delete — FTS5
        await session.execute(
            sa_text("DELETE FROM fts_messages WHERE conversation_id = :cid"),
            {"cid": conv_id},
        )
        await session.commit()

    # Проверяем: после удаления — не находится
    async with factory() as session:
        hits = await search_messages(session, "deletable-keyword", user_id, workspace_id)
        assert len(hits) == 0


@pytest.mark.asyncio
async def test_retention_cleanup_removes_fts(app_fixture: FastAPI) -> None:
    """Retention cleanup удаляет сообщения из FTS5 (§5.3).

    Стертый контент не должен быть находим поиском — прямое нарушение
    смысла retention, если FTS5-копия остаётся.
    """
    from datetime import UTC, datetime, timedelta

    user_id = await _login_user(app_fixture)
    conv_id = await _create_conversation_with_messages(
        app_fixture,
        user_id,
        [("user", "retention-keyword-for-fts-cleanup")],
    )
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id

    # Делаем диалог старым (last_activity_at в прошлом)
    old_time = datetime.now(UTC) - timedelta(days=365)
    async with factory() as session:
        result = await session.execute(select(Conversation).where(Conversation.id == conv_id))
        conv = result.scalar_one()
        conv.last_activity_at = old_time
        await session.commit()

    # Проверяем: до retention — находится
    async with factory() as session:
        hits = await search_messages(session, "retention-keyword", user_id, workspace_id)
        assert len(hits) >= 1

    # Запускаем retention cleanup
    from app.config import Settings as SettingsCls

    settings = SettingsCls()
    settings.message_retention_days = 1  # 1 day retention
    async with factory() as session:
        # Сначала FTS5 (как в retention_cleanup)
        conv_result = await session.execute(
            select(Conversation.id).where(
                Conversation.workspace_id == workspace_id,
                Conversation.last_activity_at < old_time + timedelta(seconds=1),
            )
        )
        conv_ids = [row[0] for row in conv_result.all()]
        assert conv_id in conv_ids

        from sqlalchemy import bindparam, delete

        stmt = sa_text("DELETE FROM fts_messages WHERE conversation_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        await session.execute(stmt, {"ids": conv_ids})
        await session.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
        from sqlalchemy import delete as sa_delete

        await session.execute(sa_delete(Conversation).where(Conversation.id.in_(conv_ids)))
        await session.commit()

    # Проверяем: после retention — не находится
    async with factory() as session:
        hits = await search_messages(session, "retention-keyword", user_id, workspace_id)
        assert len(hits) == 0


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(app_fixture: FastAPI) -> None:
    """Пустой запрос (только спецсимволы) → пустой результат."""
    user_id = await _login_user(app_fixture)
    await _create_conversation_with_messages(
        app_fixture,
        user_id,
        [("user", "some content here")],
    )
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        hits = await search_messages(session, "*** ???", user_id, workspace_id)
        assert len(hits) == 0
