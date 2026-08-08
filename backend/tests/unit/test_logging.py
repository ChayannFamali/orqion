"""Тест логирования: JSON-формат, структурные поля, отсутствие содержимого."""

from __future__ import annotations

import io
import json
import logging

from app.logging import JsonFormatter, setup_logging


def test_json_formatter_outputs_valid_json() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test_json")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    logger.info("test message", extra={"trace_id": "abc-123", "duration_ms": 42})

    output = stream.getvalue().strip()
    entry = json.loads(output)
    assert entry["message"] == "test message"
    assert entry["trace_id"] == "abc-123"
    assert entry["duration_ms"] == 42
    assert entry["level"] == "INFO"


def test_structured_fields_are_present_when_provided() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test_fields")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    logger.info(
        "request completed",
        extra={
            "trace_id": "trace-001",
            "user_id": "user-002",
            "model_alias": "local/qwen3-8b",
            "duration_ms": 150,
        },
    )

    entry = json.loads(stream.getvalue().strip())
    assert entry["trace_id"] == "trace-001"
    assert entry["user_id"] == "user-002"
    assert entry["model_alias"] == "local/qwen3-8b"
    assert entry["duration_ms"] == 150


def test_message_content_not_in_log() -> None:
    """Содержимое запросов не попадает в логи (AGENTS.md §6.6)."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test_no_content")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    sensitive_content = "SECRET: my api key is sk-1234567890"
    logger.info("request completed", extra={"trace_id": "t-1"})

    entry = json.loads(stream.getvalue().strip())
    assert sensitive_content not in json.dumps(entry)
    assert "sk-1234567890" not in json.dumps(entry)
    assert "SECRET" not in entry.get("message", "")


def test_setup_logging_replaces_handlers() -> None:
    setup_logging("DEBUG")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.DEBUG
