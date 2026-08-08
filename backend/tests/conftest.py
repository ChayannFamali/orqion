"""Pytest configuration: делает фикстуры доступными во всех тестах."""

from __future__ import annotations

import sys
from pathlib import Path

# Добавляем backend/ в sys.path для импортов app.* и tests.*
_backend = str(Path(__file__).resolve().parent.parent)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# re-export фикстур для автопоиска pytest
from tests.fixtures.client import api_client, app_fixture  # noqa: F401
from tests.fixtures.database import db_session, test_engine, test_settings  # noqa: F401
from tests.fixtures.factories import make_workspace  # noqa: F401
from tests.fixtures.provider import stub_provider  # noqa: F401
