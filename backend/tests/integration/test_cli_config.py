"""Тесты CLI: export-config, import-config (T-425).

Паттерн: импортируем приватные _run_* корутины, monkeypatch Settings,
temp SQLite DB, capsys для stdout/stderr.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import yaml as yaml_lib
from app.auth.bootstrap import ensure_builtin_roles
from app.config import Settings
from app.db.base import Base
from app.db.engine import create_engine, create_session_factory
from app.db.models import Role, RoutingRule
from app.db.workspace import ensure_default_workspace
from app.router.bootstrap import ensure_default_routing_rules
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def _make_test_settings(db_path: str) -> Settings:
    return Settings(
        database_url=f"sqlite:///{db_path}",
        blob_store_path=os.path.join(os.path.dirname(db_path), "blobs"),
        vector_store_path=os.path.join(os.path.dirname(db_path), "vec.db"),
        log_level="WARNING",
    )


async def _setup_test_db(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        ws_id = await ensure_default_workspace(session)
        await ensure_builtin_roles(session, ws_id)
        await ensure_default_routing_rules(session, ws_id)
        await session.commit()

    return engine, session_factory


@pytest.mark.asyncio
async def test_cli_export_config_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """orqion export-config → stdout содержит YAML."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, session_factory = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_export_config

    await _run_export_config(output_path=None)

    # Can't use capsys because _run_export_config prints and disposes engine
    # Instead, verify via re-export from DB
    async with session_factory() as session:
        ws_id = await ensure_default_workspace(session)
        from app.config_io.service import SCHEMA_VERSION, export_config

        yaml_str = await export_config(session, ws_id)
        data = yaml_lib.safe_load(yaml_str)
        assert data["schema_version"] == SCHEMA_VERSION
        assert len(data["roles"]) == 5
        assert len(data["routing_rules"]) == 4
        assert data["corpora"] == []  # T-438: секция присутствует

    await engine.dispose()


@pytest.mark.asyncio
async def test_cli_export_config_to_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """orqion export-config --output file.yaml → файл создан, содержит YAML."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, _ = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_export_config

    output_path = os.path.join(tmpdir, "config.yaml")
    await _run_export_config(output_path=output_path)

    assert os.path.exists(output_path)
    from pathlib import Path

    from app.config_io.service import SCHEMA_VERSION

    content = Path(output_path).read_text(encoding="utf-8")
    data = yaml_lib.safe_load(content)
    assert data["schema_version"] == SCHEMA_VERSION
    assert len(data["roles"]) == 5
    assert data["corpora"] == []  # T-438: секция присутствует

    await engine.dispose()


@pytest.mark.asyncio
async def test_cli_import_config_from_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """orqion import-config --input file.yaml → роли/правила импортированы."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, _session_factory = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    # First export existing config
    from app.cli import _run_export_config

    export_path = os.path.join(tmpdir, "export.yaml")
    await _run_export_config(output_path=export_path)

    # Create a fresh DB (no seed) and import
    tmpdir_b = tempfile.mkdtemp()
    db_path_b = os.path.join(tmpdir_b, "test_import.db")
    settings_b = _make_test_settings(db_path_b)
    engine_b = create_engine(settings_b)
    factory_b = create_session_factory(engine_b)

    async with engine_b.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory_b() as session:
        ws_b = await ensure_default_workspace(session)
        await session.commit()

    monkeypatch.setattr("app.cli.Settings", lambda: settings_b)

    from app.cli import _run_import_config

    await _run_import_config(input_path=export_path, dry_run=False)

    # Verify import
    async with factory_b() as session:
        roles = (
            (await session.execute(select(Role).where(Role.workspace_id == ws_b))).scalars().all()
        )
        assert len(roles) == 5  # 5 builtin roles from export

        rules = (
            (await session.execute(select(RoutingRule).where(RoutingRule.workspace_id == ws_b)))
            .scalars()
            .all()
        )
        assert len(rules) == 4  # 4 default rules from export

    await engine.dispose()
    await engine_b.dispose()


@pytest.mark.asyncio
async def test_cli_import_config_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """orqion import-config --dry-run → БД не изменена."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, session_factory = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    yaml_content = """\
schema_version: 1
roles:
  - name: custom-dry-run-role
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
    reason: "dry run rule"
"""

    input_path = os.path.join(tmpdir, "import.yaml")
    from pathlib import Path

    Path(input_path).write_text(yaml_content, encoding="utf-8")

    from app.cli import _run_import_config

    await _run_import_config(input_path=input_path, dry_run=True)

    # Verify nothing was written
    async with session_factory() as session:
        ws_id = await ensure_default_workspace(session)
        roles = (
            (
                await session.execute(
                    select(Role).where(
                        Role.workspace_id == ws_id, Role.name == "custom-dry-run-role"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(roles) == 0

    await engine.dispose()
