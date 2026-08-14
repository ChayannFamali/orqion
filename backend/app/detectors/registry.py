"""Реестр детекторов (ADR-13).

Простой Python-механизм регистрации, не setuptools entry_points.
При появлении встроенных детекторов может быть расширен до entry_points.

Из коробки реестр пуст — пользователь регистрирует свои детекторы
через register_detector().
"""

from __future__ import annotations

from app.detectors.protocol import Detector

_detectors: list[Detector] = []


def register_detector(detector: Detector) -> None:
    """Регистрирует детектор в реестре.

    Простой Python-вызов, не setuptools entry_points.
    Зафиксировано как осознанное решение (T-409): при отсутствии
    встроенных детекторов entry_points добавили бы сложность без пользы.
    """
    _detectors.append(detector)


def get_detectors() -> list[Detector]:
    """Возвращает список зарегистрированных детекторов."""
    return list(_detectors)


def clear_detectors() -> None:
    """Очищает реестр. Для тестов."""
    _detectors.clear()
