"""Встроенный regex-детектор (T-421) — референсная реализация Detector Protocol.

ОГРАНИЧЕНИЯ (ADR-13, arch.md):
- Это ПРИМЕР, не полноценная защита. Regex-паттерны ловят случайную вставку
  секретов в текст, но не выявляют смысловую конфиденциальность, не ловят
  целенаправленный обход (разбивка ключа, замена символов, кодирование).
- По прецеденту T-408 (contrib/grafana/orqion.json — «пример, не зависимость»):
  этот детектор демонстрирует, как реализовать Detector Protocol, но не
  гарантирует перехват всех возможных форматов секретов.
- Детектор НЕ регистрируется автоматически. Подключение — явный вызов
  register_detector(BuiltinRegexDetector()) в коде приложения или плагине.
  По умолчанию detectors_enabled=False (ADR-13) — даже зарегистрированный
  детектор не запускается без явного включения в настройках.

Паттерны:
1. AWS Access Key ID — AKIA + 16 alphanum (canonical format)
2. Generic API token — sk- + 20+ alphanum (universal, не привязан к провайдеру)
3. Credit card number — 13-19 digits, validated by Luhn algorithm
   (не просто regex на 16 цифр — Лун-валидность отсекает ложные срабатывания)
"""

from __future__ import annotations

import re

from app.detectors.protocol import DetectorResult


def _luhn_check(digits: str) -> bool:
    """Проверяет строку цифр по алгоритму Луна. True если валидно."""
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_API_TOKEN_RE = re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


class BuiltinRegexDetector:
    """Референсный детектор: AWS keys, API tokens, credit cards (Luhn).

    НЕ регистрируется автоматически — вызовите register_detector() явно.
    Имя: 'builtin_regex'.
    Тип: 'secrets' (всё, что ловит — секреты/credentials).
    """

    name = "builtin_regex"

    def detect(self, text: str) -> DetectorResult:
        """Сканирует текст, возвращает результат без содержимого совпадений."""
        matched_patterns: list[str] = []

        aws_matches = _AWS_KEY_RE.findall(text)
        if aws_matches:
            matched_patterns.append("aws_access_key_id")

        api_matches = _API_TOKEN_RE.findall(text)
        if api_matches:
            matched_patterns.append("api_token")

        card_matches = _CARD_RE.findall(text)
        valid_cards = 0
        for raw in card_matches:
            digits = re.sub(r"[^\d]", "", raw)
            if 13 <= len(digits) <= 19 and _luhn_check(digits):
                valid_cards += 1
        if valid_cards:
            matched_patterns.append("credit_card")

        if not matched_patterns:
            return DetectorResult(
                triggered=False,
                detector_type="secrets",
                matched_count=0,
                matched_patterns=[],
            )

        return DetectorResult(
            triggered=True,
            detector_type="secrets",
            matched_count=len(aws_matches) + len(api_matches) + valid_cards,
            matched_patterns=matched_patterns,
        )
