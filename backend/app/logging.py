"""Структурное JSON-логирование.

Поля: trace_id, user_id, model_alias, duration_ms.
Содержимое сообщений, промптов и чанков в логи не попадает никогда (AGENTS.md §6.6).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_STRUCTURED_FIELDS = ("trace_id", "user_id", "model_alias", "duration_ms")


class JsonFormatter(logging.Formatter):
    """Форматирует записи лога в JSON с структурными полями."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = type(record.exc_info[1]).__name__
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """Настраивает корневой логгер с JSON-форматтером."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
