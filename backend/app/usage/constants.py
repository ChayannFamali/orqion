"""Общие константы для usage-модуля."""

from __future__ import annotations

# BUG-008: Sentinel UUID для отсутствующего user_id/model_id в usage_daily.
# RFC 4122 §4.1.7 "Nil UUID" — зарезервирован, никогда не collide-ит с реальным.
# Заменяет NULL в PK-колонках (PostgreSQL implicit NOT NULL для PK columns).
NIL_ID = "00000000-0000-0000-0000-000000000000"
