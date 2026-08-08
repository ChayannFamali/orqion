"""Тест миграций: upgrade head → downgrade base → upgrade head."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent.parent / "alembic.ini"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "db" / "migrations"


def _make_config(db_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def test_migration_roundtrip(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path}/migrate_test.db"
    config = _make_config(db_url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
