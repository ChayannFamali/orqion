"""Общий подсчёт токенов (вынесен из чат-конвейера в Т-502).

Один энкодер и одна семантика для всех потребителей: чат-конвейер
(биллинг Т-107) и агентный модуль (оценка входных токенов на вызов
модели внутри цикла, решение 8 дизайн-ревью Т-502).
"""

from __future__ import annotations

import tiktoken

_ENCODER: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    """Возвращает BPE-энкодер. cl100k_base — универсален для большинства моделей."""
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def count_tokens(text: str) -> int:
    """Точный подсчёт токенов через tiktoken. T-107: корректен для кириллицы и кода."""
    if not text:
        return 1
    return len(_get_encoder().encode(text))
