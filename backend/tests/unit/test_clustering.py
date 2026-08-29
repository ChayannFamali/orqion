"""Т-505: ручная кластеризация (сферический k-means на numpy).

Позитивные тесты требуют установленный numpy (экстра ``orqion[graph]``)
и пропускаются без него — в дефолтном профиле кластеризация недоступна.
Тесты деградации работают в обоих окружениях: блокируют импорт numpy так
же, как приём «без authlib» (Т-404), и проверяют честный отказ.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterator

import pytest
from app.rag.clustering import cluster_vectors, is_clustering_available


def _blob(center_index: int, dim: int, count: int, seed_offset: float = 0.0) -> list[list[float]]:
    """Точки возле единичного направления с малым детерминированным шумом."""
    points: list[list[float]] = []
    for i in range(count):
        vec = [0.0] * dim
        vec[center_index] = 1.0
        # Малый шум в соседних координатах — кластера остаются разделимыми.
        for j in range(1, 4):
            vec[(center_index + j) % dim] = 0.05 * math.sin(seed_offset + i * j)
        points.append(vec)
    return points


def test_empty_input_returns_empty_labels() -> None:
    pytest.importorskip("numpy")
    assert cluster_vectors([], 5) == []


def test_k_clamped_to_number_of_points() -> None:
    pytest.importorskip("numpy")
    vectors = _blob(0, 16, 3)
    labels = cluster_vectors(vectors, k=10)
    assert len(labels) == 3
    # Каждая точка — отдельная группа: меток ровно 3 разных.
    assert len(set(labels)) == 3


def test_k_zero_or_negative_clamped_to_one() -> None:
    pytest.importorskip("numpy")
    vectors = _blob(0, 16, 4)
    labels = cluster_vectors(vectors, k=0)
    assert set(labels) == {0}


def test_two_blobs_land_in_two_clusters() -> None:
    pytest.importorskip("numpy")
    vectors = _blob(0, 32, 6) + _blob(1, 32, 6)
    labels = cluster_vectors(vectors, k=2)
    first_half = set(labels[:6])
    second_half = set(labels[6:])
    assert len(first_half) == 1
    assert len(second_half) == 1
    assert first_half != second_half


def test_deterministic_between_calls() -> None:
    pytest.importorskip("numpy")
    vectors = _blob(0, 32, 8) + _blob(1, 32, 8) + _blob(2, 32, 8)
    assert cluster_vectors(vectors, k=3) == cluster_vectors(vectors, k=3)


def test_zero_vector_does_not_crash() -> None:
    pytest.importorskip("numpy")
    vectors = [[0.0] * 16] + _blob(0, 16, 3)
    labels = cluster_vectors(vectors, k=2)
    assert len(labels) == 4


# ---------------------------------------------------------------------------
# Деградация без numpy (экстра orqion[graph] не установлена)
# ---------------------------------------------------------------------------


@pytest.fixture
def block_numpy(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Блокирует импорт numpy независимо от его реального наличия."""
    monkeypatch.setitem(sys.modules, "numpy", None)
    yield


def test_is_clustering_available_false_without_numpy(block_numpy: None) -> None:
    assert is_clustering_available() is False


def test_cluster_vectors_raises_with_install_hint_without_numpy(block_numpy: None) -> None:
    with pytest.raises(ImportError, match="orqion\\[graph\\]"):
        cluster_vectors([[1.0, 0.0]], k=1)


def test_is_clustering_available_true_with_numpy() -> None:
    pytest.importorskip("numpy")
    # numpy установлен в окружении теста.
    assert is_clustering_available() is True
