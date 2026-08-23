"""Тесты T-438: расширение YAML export/import на корпуса.

Покрытие:
- экспорт: секция corpora, пин выгружается алиасом (не UUID), сортировка по имени
- импорт на чистый инстанс: корпуса создаются с резолвом алиаса в модель
- идемпотентность: повторный импорт — нулевой diff, аудит не дублируется
- аудит только при реальном изменении (паттерн T-401)
- отказы: К2/К3 без пина, нерезолвящийся алиас, К2/К3 на внешнюю модель
- откат всего импорта при ошибке валидации корпусов (ничего не записывается)
- обратная совместимость: файл схемы v1 импортируется без секции corpora
- приёмочный roundtrip: export → очистка корпусов → import → идентичный YAML

Прямые БД-тесты используют фикстуру ``test_engine`` (TRUNCATE между
тестами — правило из BUG-015).
"""

from __future__ import annotations

import httpx
import pytest
import yaml as yaml_lib
from app.auth.bootstrap import ensure_builtin_roles
from app.auth.passwords import hash_password
from app.config_io.service import SCHEMA_VERSION, export_config, import_config
from app.db.base import Base
from app.db.engine import create_session_factory
from app.db.models import AuditLog, Corpus, Model, Provider, Role, User
from app.db.workspace import ensure_default_workspace
from app.errors import BadRequest
from app.router.bootstrap import ensure_default_routing_rules
from fastapi import FastAPI
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

CORPORA_YAML = f"""
schema_version: {SCHEMA_VERSION}
roles: []
routing_rules: []
corpora:
  - name: public-docs
    data_class: "К0"
    pinned_model_alias: null
  - name: internal
    data_class: "К1"
    pinned_model_alias: ext-model
  - name: secret
    data_class: "К2"
    pinned_model_alias: local-pin
"""


async def _setup_db(
    engine: AsyncEngine,
) -> tuple[async_sessionmaker[AsyncSession], str]:
    """Создаёт схему, workspace, builtin roles, default routing rules."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = create_session_factory(engine)
    async with factory() as session:
        ws_id = await ensure_default_workspace(session)
        await ensure_builtin_roles(session, ws_id)
        await ensure_default_routing_rules(session, ws_id)
        await session.commit()
    return factory, ws_id


async def _seed_models(factory: async_sessionmaker[AsyncSession], ws_id: str) -> tuple[str, str]:
    """Провайдер + локальная и внешняя модели. Возвращает (local_id, external_id)."""
    async with factory() as session:
        provider = Provider(
            workspace_id=ws_id,
            kind="external",
            base_url="http://stub",
            enabled=True,
        )
        session.add(provider)
        await session.flush()

        local_model = Model(
            workspace_id=ws_id,
            provider_id=provider.id,
            alias="local-pin",
            upstream_name="stub-local",
            locality="local",
        )
        external_model = Model(
            workspace_id=ws_id,
            provider_id=provider.id,
            alias="ext-model",
            upstream_name="stub-external",
            locality="external",
        )
        session.add_all([local_model, external_model])
        await session.flush()
        local_id = local_model.id
        external_id = external_model.id
        await session.commit()
    return local_id, external_id


async def _seed_admin_user(factory: async_sessionmaker[AsyncSession], ws_id: str) -> str:
    """Пользователь-актор для аудита. Возвращает user.id."""
    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "admin")
        )
        admin_role = result.scalar_one()
        user = User(
            workspace_id=ws_id,
            email="t438-actor@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=admin_role.id,
        )
        session.add(user)
        await session.flush()
        user_id = user.id
        await session.commit()
    return user_id


async def _audit_actions(
    factory: async_sessionmaker[AsyncSession], ws_id: str, action: str
) -> list[AuditLog]:
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws_id,
                AuditLog.action == action,
            )
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_corpora_with_alias(test_engine: AsyncEngine) -> None:
    """Пин выгружается алиасом модели, корпуса отсортированы по имени."""
    factory, ws_id = await _setup_db(test_engine)
    local_id, external_id = await _seed_models(factory, ws_id)

    async with factory() as session:
        session.add_all(
            [
                Corpus(
                    workspace_id=ws_id,
                    name="secret",
                    data_class="К2",
                    pinned_model_id=local_id,
                ),
                Corpus(
                    workspace_id=ws_id,
                    name="archive",
                    data_class="К0",
                    pinned_model_id=external_id,
                ),
                Corpus(workspace_id=ws_id, name="plain", data_class=None),
            ]
        )
        await session.commit()

    async with factory() as session:
        yaml_str = await export_config(session, ws_id)

    data = yaml_lib.safe_load(yaml_str)
    assert data["schema_version"] == SCHEMA_VERSION
    corpora = data["corpora"]
    assert [c["name"] for c in corpora] == ["archive", "plain", "secret"]

    by_name = {c["name"]: c for c in corpora}
    # Алиас, а не UUID
    assert by_name["secret"]["pinned_model_alias"] == "local-pin"
    assert by_name["archive"]["pinned_model_alias"] == "ext-model"
    assert by_name["plain"]["pinned_model_alias"] is None
    assert by_name["plain"]["data_class"] is None
    assert by_name["secret"]["data_class"] == "К2"


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_corpora_clean_instance(test_engine: AsyncEngine) -> None:
    """Импорт на инстанс с моделями создаёт корпуса и резолвит алиасы."""
    factory, ws_id = await _setup_db(test_engine)
    local_id, external_id = await _seed_models(factory, ws_id)

    async with factory() as session:
        result = await import_config(session, ws_id, CORPORA_YAML)
        await session.commit()

    assert result.corpora_created == 3
    assert result.corpora_updated == 0
    assert result.corpora_unchanged == 0

    async with factory() as session:
        rows = (
            (await session.execute(select(Corpus).where(Corpus.workspace_id == ws_id)))
            .scalars()
            .all()
        )
    by_name = {c.name: c for c in rows}
    assert by_name["public-docs"].data_class == "К0"
    assert by_name["public-docs"].pinned_model_id is None
    assert by_name["internal"].data_class == "К1"
    assert by_name["internal"].pinned_model_id == external_id
    assert by_name["secret"].data_class == "К2"
    assert by_name["secret"].pinned_model_id == local_id


@pytest.mark.asyncio
async def test_import_corpora_idempotent_no_duplicate_audit(
    test_engine: AsyncEngine,
) -> None:
    """Повторный импорт того же YAML — нулевой diff, аудит не пишется."""
    factory, ws_id = await _setup_db(test_engine)
    await _seed_models(factory, ws_id)
    actor_id = await _seed_admin_user(factory, ws_id)

    async with factory() as session:
        first = await import_config(session, ws_id, CORPORA_YAML, actor_user_id=actor_id)
        await session.commit()
    assert first.corpora_created == 3

    async with factory() as session:
        second = await import_config(session, ws_id, CORPORA_YAML, actor_user_id=actor_id)
        await session.commit()

    assert second.corpora_created == 0
    assert second.corpora_updated == 0
    assert second.corpora_unchanged == 3
    assert await _audit_actions(factory, ws_id, "corpus.data_class_changed") == []
    assert await _audit_actions(factory, ws_id, "corpus.pinned_model_changed") == []


@pytest.mark.asyncio
async def test_import_corpora_data_class_change_writes_audit(
    test_engine: AsyncEngine,
) -> None:
    """Смена data_class при импорте → аудит с old/new (паттерн T-401)."""
    factory, ws_id = await _setup_db(test_engine)
    await _seed_models(factory, ws_id)
    actor_id = await _seed_admin_user(factory, ws_id)

    async with factory() as session:
        await import_config(session, ws_id, CORPORA_YAML, actor_user_id=actor_id)
        await session.commit()

    changed_yaml = CORPORA_YAML.replace('data_class: "К0"', 'data_class: "К1"')
    async with factory() as session:
        result = await import_config(session, ws_id, changed_yaml, actor_user_id=actor_id)
        await session.commit()

    assert result.corpora_updated == 1
    assert result.corpora_unchanged == 2

    audits = await _audit_actions(factory, ws_id, "corpus.data_class_changed")
    assert len(audits) == 1
    assert audits[0].actor_user_id == actor_id
    assert audits[0].meta["old"] == "К0"
    assert audits[0].meta["new"] == "К1"
    assert audits[0].meta["corpus_name"] == "public-docs"


@pytest.mark.asyncio
async def test_import_corpora_pin_change_writes_audit(
    test_engine: AsyncEngine,
) -> None:
    """Смена пин-модели при импорте → аудит со старым и новым алиасом."""
    factory, ws_id = await _setup_db(test_engine)
    local_id, _ = await _seed_models(factory, ws_id)
    actor_id = await _seed_admin_user(factory, ws_id)

    async with factory() as session:
        session.add(
            Corpus(
                workspace_id=ws_id,
                name="secret",
                data_class="К2",
                pinned_model_id=local_id,
            )
        )
        await session.commit()

    # Дополнительная локальная модель для нового пина
    async with factory() as session:
        provider = (
            (await session.execute(select(Provider).where(Provider.workspace_id == ws_id)))
            .scalars()
            .one()
        )
        session.add(
            Model(
                workspace_id=ws_id,
                provider_id=provider.id,
                alias="local-pin-2",
                upstream_name="stub-local-2",
                locality="local",
            )
        )
        await session.commit()

    repin_yaml = """
schema_version: 2
roles: []
routing_rules: []
corpora:
  - name: secret
    data_class: "К2"
    pinned_model_alias: local-pin-2
"""
    async with factory() as session:
        result = await import_config(session, ws_id, repin_yaml, actor_user_id=actor_id)
        await session.commit()

    assert result.corpora_updated == 1

    audits = await _audit_actions(factory, ws_id, "corpus.pinned_model_changed")
    assert len(audits) == 1
    assert audits[0].meta["old"] == "local-pin"
    assert audits[0].meta["new"] == "local-pin-2"


@pytest.mark.asyncio
async def test_import_corpora_dry_run_counts(test_engine: AsyncEngine) -> None:
    """dry_run считает diff корпусов без записи."""
    factory, ws_id = await _setup_db(test_engine)
    local_id, _ = await _seed_models(factory, ws_id)

    async with factory() as session:
        session.add(
            Corpus(workspace_id=ws_id, name="secret", data_class="К2", pinned_model_id=local_id)
        )
        await session.commit()

    async with factory() as session:
        result = await import_config(session, ws_id, CORPORA_YAML, dry_run=True)

    assert result.corpora_created == 2
    assert result.corpora_updated == 0
    assert result.corpora_unchanged == 1

    async with factory() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(
                    select(Corpus).where(Corpus.workspace_id == ws_id).subquery()
                )
            )
        ).scalar_one()
    assert count == 1  # dry_run ничего не записал


# ---------------------------------------------------------------------------
# Отказы (валидация до записи, полный откат)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_k2_without_pin_rejected(test_engine: AsyncEngine) -> None:
    """К2 без пина — явная ошибка (ADR-12)."""
    factory, ws_id = await _setup_db(test_engine)
    await _seed_models(factory, ws_id)

    yaml_content = """
schema_version: 2
roles: []
routing_rules: []
corpora:
  - name: secret
    data_class: "К2"
    pinned_model_alias: null
"""
    async with factory() as session:
        with pytest.raises(BadRequest, match="требует pinned_model_alias"):
            await import_config(session, ws_id, yaml_content)


@pytest.mark.asyncio
async def test_import_unresolved_alias_rejected(test_engine: AsyncEngine) -> None:
    """Нерезолвящийся алиас — явная ошибка даже для К0 (не молчаливый null)."""
    factory, ws_id = await _setup_db(test_engine)
    await _seed_models(factory, ws_id)

    yaml_content = """
schema_version: 2
roles: []
routing_rules: []
corpora:
  - name: public-docs
    data_class: "К0"
    pinned_model_alias: no-such-model
"""
    async with factory() as session:
        with pytest.raises(BadRequest, match="не найдена"):
            await import_config(session, ws_id, yaml_content)


@pytest.mark.asyncio
async def test_import_k3_external_pin_rejected(test_engine: AsyncEngine) -> None:
    """К3 с пином на внешнюю модель — явная ошибка (локальность, ADR-12)."""
    factory, ws_id = await _setup_db(test_engine)
    await _seed_models(factory, ws_id)

    yaml_content = """
schema_version: 2
roles: []
routing_rules: []
corpora:
  - name: top-secret
    data_class: "К3"
    pinned_model_alias: ext-model
"""
    async with factory() as session:
        with pytest.raises(BadRequest, match="не локальная"):
            await import_config(session, ws_id, yaml_content)


@pytest.mark.asyncio
async def test_import_corpora_error_rolls_back_roles(
    test_engine: AsyncEngine,
) -> None:
    """Ошибка валидации корпусов откатывает весь импорт — роли не тронуты."""
    factory, ws_id = await _setup_db(test_engine)
    await _seed_models(factory, ws_id)

    async with factory() as session:
        admin_policy_before = (
            await session.execute(
                select(Role.policy).where(Role.workspace_id == ws_id, Role.name == "admin")
            )
        ).scalar_one()

    # YAML меняет policy admin И содержит невалидный корпус
    yaml_content = """
schema_version: 2
roles:
  - name: admin
    is_builtin: true
    policy:
      models: ["*"]
      max_input_tokens: 999999
      max_output_tokens: null
      reasoning: "off"
      budget: null
      rpm: null
      tpm: null
      corpora: ["*"]
      capabilities: ["*"]
routing_rules: []
corpora:
  - name: bad-corpus
    data_class: "К2"
    pinned_model_alias: no-such-model
"""
    async with factory() as session:
        with pytest.raises(BadRequest):
            await import_config(session, ws_id, yaml_content)
        await session.rollback()

    async with factory() as session:
        admin_policy_after = (
            await session.execute(
                select(Role.policy).where(Role.workspace_id == ws_id, Role.name == "admin")
            )
        ).scalar_one()
    assert admin_policy_after == admin_policy_before


@pytest.mark.asyncio
async def test_import_duplicate_corpus_names_rejected(
    test_engine: AsyncEngine,
) -> None:
    factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 2
roles: []
routing_rules: []
corpora:
  - name: same
    data_class: null
  - name: same
    data_class: null
"""
    async with factory() as session:
        with pytest.raises(BadRequest, match="корректна"):
            await import_config(session, ws_id, yaml_content)


# ---------------------------------------------------------------------------
# Совместимость схем
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_v1_file_compatible(test_engine: AsyncEngine) -> None:
    """Файл schema_version 1 без секции corpora импортируется как раньше."""
    factory, ws_id = await _setup_db(test_engine)

    # Существующий корпус не должен пострадать
    async with factory() as session:
        session.add(
            Corpus(workspace_id=ws_id, name="existing", data_class="К1", pinned_model_id=None)
        )
        await session.commit()

    v1_yaml = """
schema_version: 1
roles: []
routing_rules: []
"""
    async with factory() as session:
        result = await import_config(session, ws_id, v1_yaml)
        await session.commit()

    assert result.corpora_created == 0
    assert result.corpora_updated == 0
    assert result.corpora_unchanged == 0

    async with factory() as session:
        existing = (
            await session.execute(select(Corpus).where(Corpus.name == "existing"))
        ).scalar_one()
    assert existing.data_class == "К1"
    assert existing.pinned_model_id is None


@pytest.mark.asyncio
async def test_import_schema_version_3_rejected(test_engine: AsyncEngine) -> None:
    factory, ws_id = await _setup_db(test_engine)

    async with factory() as session:
        with pytest.raises(BadRequest, match="Неподдерживаемая версия схемы"):
            await import_config(session, ws_id, "schema_version: 3\nroles: []\nrouting_rules: []\n")


# ---------------------------------------------------------------------------
# Приёмочный roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_import_roundtrip_corpora(test_engine: AsyncEngine) -> None:
    """Export → удаление корпусов → import → повторный export идентичен."""
    factory, ws_id = await _setup_db(test_engine)
    await _seed_models(factory, ws_id)

    async with factory() as session:
        result = await import_config(session, ws_id, CORPORA_YAML)
        await session.commit()
    assert result.corpora_created == 3

    async with factory() as session:
        yaml_before = await export_config(session, ws_id)

    # Имитация чистого инстанса: корпуса удалены (модели — подготовлены заранее)
    async with factory() as session:
        await session.execute(delete(Corpus).where(Corpus.workspace_id == ws_id))
        await session.commit()

    async with factory() as session:
        result = await import_config(session, ws_id, yaml_before)
        await session.commit()
    assert result.corpora_created == 3

    async with factory() as session:
        yaml_after = await export_config(session, ws_id)

    assert yaml_lib.safe_load(yaml_before) == yaml_lib.safe_load(yaml_after)


# ---------------------------------------------------------------------------
# API: admin-импорт корпусов с аудитом от имени пользователя
# ---------------------------------------------------------------------------


async def _login_admin(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> str:
    """Логин админа через API. Возвращает user.id (для проверки аудита)."""
    from app.auth.sessions import COOKIE_NAME, create_session
    from app.config import Settings
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=ws_id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=ws_id,
            email="t438-api-admin@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        user_id = user.id

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user_id


@pytest.mark.asyncio
async def test_api_import_corpora_admin_counters_and_audit(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """POST /api/config/import с секцией corpora: счётчики в ответе, аудит с актором."""
    user_id = await _login_admin(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        provider = Provider(
            workspace_id=ws_id, kind="external", base_url="http://stub", enabled=True
        )
        session.add(provider)
        await session.flush()
        session.add(
            Model(
                workspace_id=ws_id,
                provider_id=provider.id,
                alias="local-pin",
                upstream_name="stub-local",
                locality="local",
            )
        )
        await session.commit()

    yaml_content = """
schema_version: 2
roles: []
routing_rules: []
corpora:
  - name: secret
    data_class: "К2"
    pinned_model_alias: local-pin
"""
    resp = await api_client.post("/api/config/import", json={"yaml": yaml_content})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["corpora_created"] == 1
    assert data["corpora_updated"] == 0
    assert data["corpora_unchanged"] == 0

    # Повторный импорт — идемпотентно, без нового аудита
    resp = await api_client.post("/api/config/import", json={"yaml": yaml_content})
    assert resp.status_code == 200
    assert resp.json()["corpora_unchanged"] == 1

    # Смена класса через импорт → аудит с актором-админом
    changed = yaml_content.replace('data_class: "К2"', 'data_class: "К3"')
    resp = await api_client.post("/api/config/import", json={"yaml": changed})
    assert resp.status_code == 200
    assert resp.json()["corpora_updated"] == 1

    audits = await _audit_actions(factory, ws_id, "corpus.data_class_changed")
    assert len(audits) == 1
    assert audits[0].actor_user_id == user_id
    assert audits[0].meta["old"] == "К2"
    assert audits[0].meta["new"] == "К3"
