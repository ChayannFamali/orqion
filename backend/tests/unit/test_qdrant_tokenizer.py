"""Тесты токенизатора для Qdrant sparse-поиска (T-213).

Токенизатор — чистая функция, не требует qdrant-client.
Проверки: детерминизм, пустой текст, повторяемость, не-ASCII,
формат выходных данных.
"""

from __future__ import annotations

from app.rag.qdrant_store import _hash_token, _tokenize

# ---------------------------------------------------------------------------
# Детерминизм
# ---------------------------------------------------------------------------


def test_tokenize_deterministic() -> None:
    """Одинаковый текст → одинаковые indices и values."""
    indices1, values1 = _tokenize("hello world")
    indices2, values2 = _tokenize("hello world")
    assert indices1 == indices2
    assert values1 == values2


def test_tokenize_different_texts() -> None:
    """Разные тексты → разные indices."""
    indices1, _ = _tokenize("hello world")
    indices2, _ = _tokenize("foo bar baz")
    assert indices1 != indices2


# ---------------------------------------------------------------------------
# Пустой и краевые случаи
# ---------------------------------------------------------------------------


def test_tokenize_empty() -> None:
    """Пустой текст — пустые списки."""
    indices, values = _tokenize("")
    assert indices == []
    assert values == []


def test_tokenize_only_punctuation() -> None:
    """Текст только с пунктуацией — пустые списки (\\w+ не матчит)."""
    indices, values = _tokenize("!!! ??? ...")
    assert indices == []
    assert values == []


def test_tokenize_whitespace_only() -> None:
    """Текст только с пробелами — пустые списки."""
    indices, values = _tokenize("   \t  \n  ")
    assert indices == []
    assert values == []


# ---------------------------------------------------------------------------
# TF (term frequency)
# ---------------------------------------------------------------------------


def test_tokenize_repeated_tokens() -> None:
    """Повторяющиеся токены — TF увеличивается."""
    indices1, values1 = _tokenize("hello")
    indices2, values2 = _tokenize("hello hello hello")

    # Одинаковые indices (те же токены)
    assert indices1 == indices2
    # values[0] для "hello hello hello" в 3 раза больше
    assert values2[0] == 3.0 * values1[0]


def test_tokenize_multiple_unique_tokens() -> None:
    """Несколько уникальных токенов — несколько indices."""
    indices, values = _tokenize("hello world foo")
    assert len(indices) == 3
    assert len(values) == 3
    # Все TF = 1.0 (каждый токен встречается один раз)
    assert all(v == 1.0 for v in values)


def test_tokenize_mixed_repetition() -> None:
    """Смешанные повторения: hello hello world → hello TF=2, world TF=1."""
    indices, values = _tokenize("hello hello world")
    assert len(indices) == 2
    # indices отсортированы, values соответствуют
    # hello встречается 2 раза, world 1 раз
    assert 2.0 in values
    assert 1.0 in values


# ---------------------------------------------------------------------------
# Формат выходных данных
# ---------------------------------------------------------------------------


def test_tokenize_indices_are_non_negative() -> None:
    """Все indices — неотрицательные int."""
    indices, _ = _tokenize("some text here")
    assert all(isinstance(i, int) for i in indices)
    assert all(i >= 0 for i in indices)


def test_tokenize_indices_sorted() -> None:
    """Indices отсортированы по возрастанию."""
    indices, _ = _tokenize("zebra apple mango")
    assert indices == sorted(indices)


def test_tokenize_indices_values_same_length() -> None:
    """Длины indices и values совпадают."""
    indices, values = _tokenize("hello world foo bar")
    assert len(indices) == len(values)


def test_tokenize_case_insensitive() -> None:
    """Токенизация case-insensitive: Hello и hello → один indices."""
    indices1, _ = _tokenize("Hello")
    indices2, _ = _tokenize("hello")
    assert indices1 == indices2


# ---------------------------------------------------------------------------
# Не-ASCII
# ---------------------------------------------------------------------------


def test_tokenize_unicode() -> None:
    """Unicode токены обрабатываются (\\w+ включает Unicode для str)."""
    indices, values = _tokenize("привет мир")
    assert len(indices) == 2
    assert len(values) == 2


def test_tokenize_unicode_deterministic() -> None:
    """Unicode токенизация детерминирована."""
    indices1, values1 = _tokenize("привет мир")
    indices2, values2 = _tokenize("привет мир")
    assert indices1 == indices2
    assert values1 == values2


# ---------------------------------------------------------------------------
# _hash_token
# ---------------------------------------------------------------------------


def test_hash_token_deterministic() -> None:
    """Хеш токена детерминирован."""
    h1 = _hash_token("test")
    h2 = _hash_token("test")
    assert h1 == h2


def test_hash_token_different_tokens() -> None:
    """Разные токены — разные хеши (с высокой вероятностью)."""
    h1 = _hash_token("hello")
    h2 = _hash_token("world")
    assert h1 != h2


def test_hash_token_uint32() -> None:
    """Хеш в диапазоне uint32 [0, 2^32 - 1]."""
    h = _hash_token("anything")
    assert 0 <= h <= 2**32 - 1
