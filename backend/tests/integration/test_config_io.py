"""Тесты config_io: export/import ролей и routing rules (T-425).

Покрытие:
- export_config: базовый, пустой инстанс (только seed)
- import_config: чистый инстанс, idempotent, builtin upsert, custom role, sync rules
- валидация: policy, duplicate orders, bad schema_version, missing schema_version
- dry_run, warnings (loose references), rollback on error
- приёмочный roundtrip: export A → import B → export B' → YAML == YAML'

Прямые БД-тесты используют фикстуру ``test_engine``: на общей PostgreSQL
из ``ORQION_DATABASE_URL`` она делает TRUNCATE между тестами, иначе
закоммиченные роли утекают в следующие тесты (на свежем SQLite-файле
это не проявляется).
"""

from __future__ import annotations

import pytest
import yaml as yaml_lib
from app.auth.bootstrap import ensure_builtin_roles
from app.config import Settings
from app.config_io.service import SCHEMA_VERSION, export_config, import_config
from app.db.base import Base
from app.db.engine import create_engine, create_session_factory
from app.db.models import Role, RoutingRule, User
from app.db.workspace import ensure_default_workspace
from app.errors import BadRequest
from app.router.bootstrap import ensure_default_routing_rules
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


async def _setup_db(
    engine: AsyncEngine,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession], str]:
    """Создаёт схему, workspace, builtin roles, default routing rules."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = create_session_factory(engine)
    async with factory() as session:
        ws_id = await ensure_default_workspace(session)
        await ensure_builtin_roles(session, ws_id)
        await ensure_default_routing_rules(session, ws_id)
        await session.commit()
    return engine, factory, ws_id


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_config_basic(test_engine: AsyncEngine) -> None:
    """Export создаёт валидный YAML с schema_version, roles, routing_rules."""
    _, factory, ws_id = await _setup_db(test_engine)

    async with factory() as session:
        yaml_str = await export_config(session, ws_id)

    data = yaml_lib.safe_load(yaml_str)
    assert data["schema_version"] == SCHEMA_VERSION
    assert len(data["roles"]) == 5  # 5 builtin roles
    assert len(data["routing_rules"]) == 4  # 4 default rules

    role_names = [r["name"] for r in data["roles"]]
    assert "support" in role_names
    assert "developer" in role_names
    assert "admin" in role_names

    rule_orders = [r["order"] for r in data["routing_rules"]]
    assert rule_orders == sorted(rule_orders)


@pytest.mark.asyncio
async def test_export_config_empty(test_engine: AsyncEngine) -> None:
    """Export на инстансе только с seed → YAML содержит builtin роли + default rules."""
    _, factory, ws_id = await _setup_db(test_engine)

    async with factory() as session:
        yaml_str = await export_config(session, ws_id)

    data = yaml_lib.safe_load(yaml_str)
    assert data["schema_version"] == SCHEMA_VERSION
    assert len(data["roles"]) == 5
    assert len(data["routing_rules"]) == 4


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_config_clean(test_engine: AsyncEngine) -> None:
    """Импорт на чистый инстанс → роли и правила созданы."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 1
roles:
  - name: custom-role
    is_builtin: false
    policy:
      models: ["local/*"]
      max_input_tokens: 32000
      max_output_tokens: 4000
      reasoning: "off"
      budget:
        tokens_month: 1000000
        cost_month: 5
      rpm: 20
      tpm: 10000
      corpora: ["public"]
      capabilities: ["chat", "upload"]
routing_rules:
  - order: 50
    is_default: false
    is_terminal: false
    when_corpus_class: null
    when_role: null
    when_task: "code"
    when_model_alias: null
    to_models: ["local/*"]
    allow_locality: null
    fallback_models: []
    reason: "custom rule"
"""

    async with factory() as session:
        result = await import_config(session, ws_id, yaml_content)
        await session.commit()

    assert result.roles_created == 1
    assert result.roles_updated == 0
    assert result.roles_unchanged == 0
    assert result.routing_rules_replaced is True
    assert result.routing_rules_count == 1

    async with factory() as session:
        roles = (
            (await session.execute(select(Role).where(Role.workspace_id == ws_id))).scalars().all()
        )
        role_names = [r.name for r in roles]
        assert "custom-role" in role_names

        rules = (
            (await session.execute(select(RoutingRule).where(RoutingRule.workspace_id == ws_id)))
            .scalars()
            .all()
        )
        assert len(rules) == 1
        assert rules[0].order == 50
        assert rules[0].reason == "custom rule"


@pytest.mark.asyncio
async def test_import_config_idempotent(test_engine: AsyncEngine) -> None:
    """Двойной импорт того же YAML → второй прогон: 0 created, 0 updated, all unchanged."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 1
roles:
  - name: custom-role
    is_builtin: false
    policy:
      models: ["local/*"]
      max_input_tokens: 32000
      max_output_tokens: 4000
      reasoning: "off"
      budget:
        tokens_month: 1000000
        cost_month: 5
      rpm: 20
      tpm: 10000
      corpora: ["public"]
      capabilities: ["chat", "upload"]
routing_rules:
  - order: 50
    is_default: false
    is_terminal: false
    when_corpus_class: null
    when_role: "custom-role"
    when_task: null
    when_model_alias: null
    to_models: ["local/*"]
    allow_locality: null
    fallback_models: []
    reason: "custom rule"
"""

    async with factory() as session:
        result1 = await import_config(session, ws_id, yaml_content)
        await session.commit()

    async with factory() as session:
        result2 = await import_config(session, ws_id, yaml_content)
        await session.commit()

    assert result1.roles_created == 1
    assert result1.routing_rules_replaced is True

    assert result2.roles_created == 0
    assert result2.roles_updated == 0
    assert result2.roles_unchanged == 1
    assert result2.routing_rules_replaced is False


@pytest.mark.asyncio
async def test_import_config_builtin_upsert(test_engine: AsyncEngine) -> None:
    """Builtin роли обновляются (policy), не дублируются."""
    _, factory, ws_id = await _setup_db(test_engine)

    # Modify builtin "developer" policy in YAML
    yaml_content = """
schema_version: 1
roles:
  - name: developer
    is_builtin: true
    policy:
      models: ["local/*"]
      max_input_tokens: 128000
      max_output_tokens: 16000
      reasoning: "on"
      budget:
        tokens_month: 10000000
        cost_month: 20
      rpm: 120
      tpm: 120000
      corpora: ["public", "team"]
      capabilities: ["chat", "upload", "custom_prompts", "view_traces"]
routing_rules: []
"""

    async with factory() as session:
        result = await import_config(session, ws_id, yaml_content)
        await session.commit()

    assert result.roles_created == 0
    assert result.roles_updated == 1
    assert result.roles_unchanged == 0

    async with factory() as session:
        dev_roles = (
            (
                await session.execute(
                    select(Role).where(Role.workspace_id == ws_id, Role.name == "developer")
                )
            )
            .scalars()
            .all()
        )
        assert len(dev_roles) == 1  # not duplicated
        assert dev_roles[0].policy["max_input_tokens"] == 128000


@pytest.mark.asyncio
async def test_import_config_custom_role_created(test_engine: AsyncEngine) -> None:
    """Custom роль создаётся."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 1
roles:
  - name: intern
    is_builtin: false
    policy:
      models: ["local/*"]
      max_input_tokens: 8000
      max_output_tokens: 1000
      reasoning: "off"
      budget:
        tokens_month: 500000
        cost_month: 0
      rpm: 15
      tpm: 5000
      corpora: ["public"]
      capabilities: ["chat"]
routing_rules: []
"""

    async with factory() as session:
        result = await import_config(session, ws_id, yaml_content)
        await session.commit()

    assert result.roles_created == 1


@pytest.mark.asyncio
async def test_import_config_routing_rules_sync(test_engine: AsyncEngine) -> None:
    """Existing routing rules заменяются на YAML."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 1
roles: []
routing_rules:
  - order: 0
    is_default: false
    is_terminal: true
    when_corpus_class: ["К1"]
    when_role: null
    when_task: null
    when_model_alias: null
    to_models: null
    allow_locality: ["local"]
    fallback_models: null
    reason: "replaced rule"
  - order: 99
    is_default: true
    is_terminal: true
    when_corpus_class: null
    when_role: null
    when_task: null
    when_model_alias: null
    to_models: null
    allow_locality: null
    fallback_models: null
    reason: "replaced default"
"""

    async with factory() as session:
        result = await import_config(session, ws_id, yaml_content)
        await session.commit()

    assert result.routing_rules_replaced is True
    assert result.routing_rules_count == 2

    async with factory() as session:
        rules = (
            (
                await session.execute(
                    select(RoutingRule)
                    .where(RoutingRule.workspace_id == ws_id)
                    .order_by(RoutingRule.order)
                )
            )
            .scalars()
            .all()
        )
        assert len(rules) == 2
        assert rules[0].reason == "replaced rule"
        assert rules[1].reason == "replaced default"


@pytest.mark.asyncio
async def test_import_config_routing_rules_noop(test_engine: AsyncEngine) -> None:
    """Если existing == YAML → routing_rules_replaced=False."""
    _, factory, ws_id = await _setup_db(test_engine)

    # Export existing, then re-import
    async with factory() as session:
        yaml_str = await export_config(session, ws_id)

    async with factory() as session:
        result = await import_config(session, ws_id, yaml_str)
        await session.commit()

    assert result.routing_rules_replaced is False


@pytest.mark.asyncio
async def test_import_config_policy_validation(test_engine: AsyncEngine) -> None:
    """Невалидный policy → abort, ничего не меняется."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 1
roles:
  - name: bad-role
    is_builtin: false
    policy:
      models: ["local/*"]
      max_input_tokens: -100
      reasoning: "off"
      corpora: ["public"]
      capabilities: ["chat"]
routing_rules: []
"""

    async with factory() as session:
        with pytest.raises(BadRequest):
            await import_config(session, ws_id, yaml_content)


@pytest.mark.asyncio
async def test_import_config_duplicate_orders(test_engine: AsyncEngine) -> None:
    """Duplicate `order` в YAML → abort."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 1
roles: []
routing_rules:
  - order: 5
    is_default: false
    is_terminal: false
    reason: "rule A"
  - order: 5
    is_default: false
    is_terminal: false
    reason: "rule B"
"""

    async with factory() as session:
        with pytest.raises(BadRequest):
            await import_config(session, ws_id, yaml_content)


@pytest.mark.asyncio
async def test_import_config_bad_schema_version(test_engine: AsyncEngine) -> None:
    """schema_version=2 → reject."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 2
roles: []
routing_rules: []
"""

    async with factory() as session:
        with pytest.raises(BadRequest):
            await import_config(session, ws_id, yaml_content)


@pytest.mark.asyncio
async def test_import_config_missing_schema_version(test_engine: AsyncEngine) -> None:
    """Нет schema_version → reject."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
roles: []
routing_rules: []
"""

    async with factory() as session:
        with pytest.raises(BadRequest):
            await import_config(session, ws_id, yaml_content)


@pytest.mark.asyncio
async def test_import_config_dry_run(test_engine: AsyncEngine) -> None:
    """--dry-run → возвращает diff, не пишет в БД."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 1
roles:
  - name: custom-role
    is_builtin: false
    policy:
      models: ["local/*"]
      max_input_tokens: 32000
      max_output_tokens: 4000
      reasoning: "off"
      budget:
        tokens_month: 1000000
        cost_month: 5
      rpm: 20
      tpm: 10000
      corpora: ["public"]
      capabilities: ["chat"]
routing_rules:
  - order: 50
    is_default: false
    is_terminal: false
    reason: "custom rule"
"""

    async with factory() as session:
        result = await import_config(session, ws_id, yaml_content, dry_run=True)
        # NOTE: no commit

    assert result.roles_created == 1
    assert result.routing_rules_replaced is True

    # Verify nothing was written
    async with factory() as session:
        roles = (
            (
                await session.execute(
                    select(Role).where(Role.workspace_id == ws_id, Role.name == "custom-role")
                )
            )
            .scalars()
            .all()
        )
        assert len(roles) == 0


@pytest.mark.asyncio
async def test_import_config_warnings_role_not_found(test_engine: AsyncEngine) -> None:
    """when_role ссылается на несуществующую роль → warning, правило создаётся."""
    _, factory, ws_id = await _setup_db(test_engine)

    yaml_content = """
schema_version: 1
roles: []
routing_rules:
  - order: 10
    is_default: false
    is_terminal: true
    when_role: "nonexistent-role"
    reason: "rule with dangling ref"
"""

    async with factory() as session:
        result = await import_config(session, ws_id, yaml_content)
        await session.commit()

    assert len(result.warnings) >= 1
    assert any("nonexistent-role" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_import_config_rollback_on_error(test_engine: AsyncEngine) -> None:
    """Ошибка в середине → rollback, partial changes не сохраняются."""
    _, factory, ws_id = await _setup_db(test_engine)

    # Valid role first, then invalid policy on second role
    yaml_content = """
schema_version: 1
roles:
  - name: good-role
    is_builtin: false
    policy:
      models: ["local/*"]
      max_input_tokens: 32000
      max_output_tokens: 4000
      reasoning: "off"
      budget:
        tokens_month: 1000000
        cost_month: 5
      rpm: 20
      tpm: 10000
      corpora: ["public"]
      capabilities: ["chat"]
  - name: bad-role
    is_builtin: false
    policy:
      models: ["local/*"]
      max_input_tokens: -50
      reasoning: "off"
      corpora: ["public"]
      capabilities: ["chat"]
routing_rules: []
"""

    async with factory() as session:
        with pytest.raises(BadRequest):
            await import_config(session, ws_id, yaml_content)
        # No commit — session rolled back

    # Verify good-role was NOT persisted (rollback)
    async with factory() as session:
        roles = (
            (
                await session.execute(
                    select(Role).where(Role.workspace_id == ws_id, Role.name == "good-role")
                )
            )
            .scalars()
            .all()
        )
        assert len(roles) == 0


# ---------------------------------------------------------------------------
# Приёмочный тест: roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_import_roundtrip_identical(test_engine: AsyncEngine) -> None:
    """Export с инстанса A → import на чистый инстанс B → export B' → YAML идентичен.

    Исключённые поля: id, workspace_id, created_at (instance-specific).
    """
    # --- Instance A: seed + custom role + custom routing rule ---
    engine_a = test_engine
    _, factory_a, ws_id_a = await _setup_db(engine_a)

    # Add custom role and custom routing rule to instance A
    async with factory_a() as session:
        from app.policy.presets import BUILTIN_ROLES

        custom_policy = BUILTIN_ROLES["developer"].model_dump(mode="json")
        custom_policy["max_input_tokens"] = 96000
        session.add(
            Role(
                workspace_id=ws_id_a,
                name="senior-developer",
                is_builtin=False,
                policy=custom_policy,
            )
        )
        session.add(
            RoutingRule(
                workspace_id=ws_id_a,
                order=50,
                is_default=False,
                is_terminal=False,
                when_role="senior-developer",
                to_models=["external/*"],
                reason="senior dev gets external models",
            )
        )
        await session.commit()

    # Export from A
    async with factory_a() as session:
        yaml_a = await export_config(session, ws_id_a)

    # --- Instance B: fresh DB (only schema, no seed) ---
    import os
    import tempfile

    tmpdir_b = tempfile.mkdtemp()
    db_path_b = os.path.join(tmpdir_b, "roundtrip_b.db")

    settings_b = Settings(
        database_url=f"sqlite:///{db_path_b}",
        blob_store_path=os.path.join(tmpdir_b, "blobs"),
        vector_store_path=os.path.join(tmpdir_b, "vec.db"),
        log_level="WARNING",
    )
    engine_b = create_engine(settings_b)
    factory_b = create_session_factory(engine_b)

    async with engine_b.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory_b() as session:
        ws_id_b = await ensure_default_workspace(session)
        await session.commit()

    # Import A's YAML into B
    async with factory_b() as session:
        result = await import_config(session, ws_id_b, yaml_a)
        await session.commit()

    assert result.roles_created > 0
    assert result.routing_rules_replaced is True

    # Export from B
    async with factory_b() as session:
        yaml_b = await export_config(session, ws_id_b)

    # Compare — YAML should be identical
    data_a = yaml_lib.safe_load(yaml_a)
    data_b = yaml_lib.safe_load(yaml_b)
    assert data_a == data_b

    # Also verify DB state matches by business fields
    async with factory_b() as session:
        roles_b = (
            (
                await session.execute(
                    select(Role)
                    .where(Role.workspace_id == ws_id_b)
                    .order_by(Role.is_builtin.desc(), Role.name)
                )
            )
            .scalars()
            .all()
        )
        rules_b = (
            (
                await session.execute(
                    select(RoutingRule)
                    .where(RoutingRule.workspace_id == ws_id_b)
                    .order_by(RoutingRule.order)
                )
            )
            .scalars()
            .all()
        )

    # Verify all roles from YAML exist in B with matching business fields
    for yaml_role in data_b["roles"]:
        db_role = next(r for r in roles_b if r.name == yaml_role["name"])
        assert db_role.is_builtin == yaml_role["is_builtin"]
        assert db_role.policy == yaml_role["policy"]

    # Verify all routing rules from YAML exist in B with matching business fields
    for yaml_rule in data_b["routing_rules"]:
        db_rule = next(r for r in rules_b if r.order == yaml_rule["order"])
        assert db_rule.is_default == yaml_rule["is_default"]
        assert db_rule.is_terminal == yaml_rule["is_terminal"]
        assert db_rule.when_corpus_class == yaml_rule["when_corpus_class"]
        assert db_rule.when_role == yaml_rule["when_role"]
        assert db_rule.when_task == yaml_rule["when_task"]
        assert db_rule.when_model_alias == yaml_rule["when_model_alias"]
        assert db_rule.to_models == yaml_rule["to_models"]
        assert db_rule.allow_locality == yaml_rule["allow_locality"]
        assert db_rule.fallback_models == yaml_rule["fallback_models"]
        assert db_rule.reason == yaml_rule["reason"]

    # engine_a == фикстура test_engine — её освобождает сама фикстура.
    await engine_b.dispose()


# ---------------------------------------------------------------------------
# API access control
# ---------------------------------------------------------------------------

import httpx
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from fastapi import FastAPI


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> str:
    """Логинится как admin. Находит существующий admin-role или создаёт."""
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        result = await session.execute(
            select(Role).where(
                Role.workspace_id == ws_id,
                Role.name == "admin",
            )
        )
        role = result.scalar_one_or_none()
        if role is None:
            role = Role(
                workspace_id=ws_id,
                name="admin",
                is_builtin=True,
                policy=BUILTIN_ROLES["admin"].model_dump(),
            )
            session.add(role)
            await session.flush()

        password = "admin-password-123"
        user = User(
            workspace_id=ws_id,
            email="admin@orqion.local",
            password_hash=hash_password(password),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _login_as_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
) -> None:
    """Логинит пользователя с заданной ролью (non-admin)."""
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        result = await session.execute(
            select(Role).where(
                Role.workspace_id == ws_id,
                Role.name == role_name,
            )
        )
        role = result.scalar_one_or_none()
        if role is None:
            role = Role(
                workspace_id=ws_id,
                name=role_name,
                is_builtin=True,
                policy=BUILTIN_ROLES[role_name].model_dump(),
            )
            session.add(role)
            await session.flush()

        user = User(
            workspace_id=ws_id,
            email=f"config-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


@pytest.mark.asyncio
async def test_api_export_config_unauthorized(
    api_client: httpx.AsyncClient,
) -> None:
    """GET /api/config/export без auth → 401."""
    resp = await api_client.get("/api/config/export")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_export_config_non_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/config/export non-admin → 404."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/config/export")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_api_import_config_unauthorized(
    api_client: httpx.AsyncClient,
) -> None:
    """POST /api/config/import без auth → 401."""
    resp = await api_client.post(
        "/api/config/import",
        json={"yaml": "schema_version: 1\nroles: []\nrouting_rules: []\n"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_import_config_non_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/config/import non-admin → 404."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.post(
        "/api/config/import",
        json={"yaml": "schema_version: 1\nroles: []\nrouting_rules: []\n"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_api_export_import_admin_ok(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin может export и import — оба эндпоинта возвращают 200."""
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.get("/api/config/export")
    assert resp.status_code == 200
    yaml_str = resp.json()["yaml"]
    assert "schema_version" in yaml_str

    resp = await api_client.post(
        "/api/config/import",
        json={"yaml": yaml_str, "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "roles_created" in data
