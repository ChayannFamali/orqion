"""Pytest configuration: делает фикстуры доступными во всех тестах."""

from __future__ import annotations

import sys
from pathlib import Path

# Добавляем backend/ в sys.path для импортов app.* и tests.*
_backend = str(Path(__file__).resolve().parent.parent)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# re-export фикстур для автопоиска pytest
from tests.fixtures.blob import blob_store, blob_store_factory  # noqa: F401
from tests.fixtures.client import (  # noqa: F401
    api_client,
    app_fixture,
    app_provider_fixture,
    provider_api_client,
    provider_settings,
)
from tests.fixtures.database import db_session, test_engine, test_settings  # noqa: F401
from tests.fixtures.factories import make_workspace  # noqa: F401
from tests.fixtures.provider import stub_provider  # noqa: F401
