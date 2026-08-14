"""Интерфейс подключаемого детектора (ADR-13).

Детекторы персональных данных и секретов — опциональная страховка.

ОГРАНИЧЕНИЯ (ADR-13, arch.md):
- Не выявляют смысловую конфиденциальность
- Не выявляют имена без сопутствующих документов
- Не выявляют содержимое неразобранных вложений
- Не выявляют целенаправленный обход
- Страховка от случайной вставки, не средство защиты от умысла

При срабатывании фиксируются: факт, пользователь, модель, типы
сработавших детекторов и хеш запроса. Содержимое не логируется.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class DetectorResult:
    """Результат работы детектора.

    matched_patterns — список имён паттернов (e.g. "credit_card", "api_key"),
    НЕ содержимое совпадений.
    """

    triggered: bool
    detector_type: str  # "personal_data" | "secrets" | "custom"
    matched_count: int
    matched_patterns: list[str]


@runtime_checkable
class Detector(Protocol):
    """Плагин детектора персональных данных/секретов (ADR-13).

    Сканирует текст, отправляемый во внешнего провайдера (outbound).
    Не блокирует запрос — только логирует срабатывание.
    """

    name: str

    def detect(self, text: str) -> DetectorResult:
        """Сканирует текст. Возвращает результат (сработал/нет + тип)."""
        ...
