"""Запуск детекторов на исходящие сообщения (ADR-13).

Вызывается в fallback-цикле execute_complete/execute_stream перед
отправкой во внешнего провайдера (provider.kind != "local").
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit
from app.config import Settings
from app.db.models import User
from app.detectors.registry import get_detectors

logger = logging.getLogger(__name__)


async def run_detectors(
    session: AsyncSession,
    settings: Settings,
    user: User,
    model_id: str,
    conversation_id: str | None,
    messages: list[dict[str, str]],
    provider_kind: str,
) -> None:
    """Запускает детекторы на messages перед отправкой внешнему провайдеру.

    Только для внешних провайдеров (provider_kind != "local").
    Только если detectors_enabled=True.
    Не блокирует запрос — только логирует срабатывание.

    Сканирует весь messages list (системная инструкция + RAG-фрагменты +
    история диалога + текущий запрос). При длинном диалоге одно и то же
    содержимое может срабатывать повторно — осознанное поведение (ADR-13).
    """
    if not settings.detectors_enabled:
        return
    if provider_kind == "local":
        return

    detectors = get_detectors()
    if not detectors:
        return

    # Собираем текст для сканирования
    text_parts = [m.get("content", "") for m in messages]
    full_text = "\n".join(text_parts)
    request_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()[:16]

    triggered_detectors: list[dict[str, Any]] = []

    for detector in detectors:
        try:
            result = detector.detect(full_text)
            if result.triggered:
                triggered_detectors.append(
                    {
                        "name": detector.name,
                        "detector_type": result.detector_type,
                        "matched_count": result.matched_count,
                        "matched_patterns": result.matched_patterns,
                    }
                )
        except Exception:
            logger.exception(
                "detector_error",
                extra={"detector_name": getattr(detector, "name", "unknown")},
            )

    if triggered_detectors:
        detector_types = [d["detector_type"] for d in triggered_detectors]
        detector_names = [d["name"] for d in triggered_detectors]

        await write_audit(
            session,
            workspace_id=user.workspace_id,
            actor_user_id=user.id,
            action="security.detector_triggered",
            object_type="conversation",
            object_id=conversation_id or "",
            meta={
                "detector_names": detector_names,
                "detector_types": detector_types,
                "model_id": model_id,
                "request_hash": request_hash,
                "matched_counts": [d["matched_count"] for d in triggered_detectors],
                "matched_patterns": [d["matched_patterns"] for d in triggered_detectors],
            },
        )
