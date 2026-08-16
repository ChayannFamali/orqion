"""Тесты встроенного regex-детектора (T-421).

Позитивные: ловит валидные образцы.
Негативные: не ловит похожий, но невалидный текст (Лун, обрезанные ключи).
"""

from __future__ import annotations

from app.detectors.builtin import BuiltinRegexDetector


def test_aws_access_key_detected() -> None:
    d = BuiltinRegexDetector()
    result = d.detect("Here is the key: AKIAIOSFODNN7EXAMPLE use it wisely")
    assert result.triggered
    assert "aws_access_key_id" in result.matched_patterns
    assert result.detector_type == "secrets"
    assert result.matched_count >= 1


def test_aws_key_not_triggered_on_partial() -> None:
    d = BuiltinRegexDetector()
    result = d.detect("AKIA123 too short")
    assert not result.triggered


def test_aws_key_not_triggered_on_lowercase() -> None:
    d = BuiltinRegexDetector()
    result = d.detect("akiaIOSFODNN7EXAMPLE")
    assert not result.triggered


def test_api_token_detected() -> None:
    d = BuiltinRegexDetector()
    result = d.detect("sk-abc123def456ghi789jkl012mno")
    assert result.triggered
    assert "api_token" in result.matched_patterns


def test_api_token_not_triggered_on_short() -> None:
    d = BuiltinRegexDetector()
    result = d.detect("sk-short")
    assert not result.triggered


def test_credit_card_detected() -> None:
    d = BuiltinRegexDetector()
    # 4242 4242 4242 4242 — известная тестовая карта, проходит Луна
    result = d.detect("My card is 4242 4242 4242 4242")
    assert result.triggered
    assert "credit_card" in result.matched_patterns


def test_credit_card_with_dashes_detected() -> None:
    d = BuiltinRegexDetector()
    result = d.detect("4242-4242-4242-4242")
    assert result.triggered
    assert "credit_card" in result.matched_patterns


def test_credit_card_not_triggered_on_random_digits() -> None:
    """16 случайных цифр без Лун-валидности не должны триггерить."""
    d = BuiltinRegexDetector()
    result = d.detect("1234 5678 9012 3456")
    assert not result.triggered


def test_credit_card_not_triggered_on_short_number() -> None:
    d = BuiltinRegexDetector()
    result = d.detect("1234 5678")
    assert not result.triggered


def test_no_secrets_in_clean_text() -> None:
    d = BuiltinRegexDetector()
    result = d.detect("This is a normal message about the weather today.")
    assert not result.triggered
    assert result.matched_count == 0
    assert result.matched_patterns == []


def test_multiple_secrets_detected() -> None:
    d = BuiltinRegexDetector()
    text = "Key: AKIAIOSFODNN7EXAMPLE and token: sk-abc123def456ghi789jkl012mno"
    result = d.detect(text)
    assert result.triggered
    assert "aws_access_key_id" in result.matched_patterns
    assert "api_token" in result.matched_patterns
    assert result.matched_count >= 2


def test_detector_name() -> None:
    d = BuiltinRegexDetector()
    assert d.name == "builtin_regex"


def test_result_does_not_contain_matched_content() -> None:
    """DetectorResult содержит только имена паттернов, не содержимое."""
    d = BuiltinRegexDetector()
    result = d.detect("AKIAIOSFODNN7EXAMPLE")
    assert result.triggered
    for pattern_name in result.matched_patterns:
        assert "AKIA" not in pattern_name
