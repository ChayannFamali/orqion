"""Кластеризация векторов для графа связей документов (Т-505).

Ручной сферический k-means на numpy (без scikit-learn): эмбеддинги
нормализованы, близость — косинусная (скалярное произведение),
инициализация k-means++, итерации Ллойда до сходимости. Детерминизм —
фиксированный генератор ``default_rng(seed)``: повторный вызов на тех же
данных даёт те же кластеры.

numpy — опциональная зависимость (экстра ``orqion[graph]``, не core).
Импорт ленивый внутри функций: без экстры модуль импортируется, а
``is_clustering_available()`` честно сообщает о недоступности — паттерн
деградации реранкера (Т-217) и диагностики окружения (Т-444).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


def is_clustering_available() -> bool:
    """True если numpy установлен (экстра orqion[graph])."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def cluster_vectors(
    vectors: Sequence[Sequence[float]],
    k: int,
    *,
    seed: int = 0,
    max_iterations: int = 100,
) -> list[int]:
    """Сферический k-means по строкам матрицы; возвращает метки кластеров.

    Метки — 0..k'-1, где k' = min(k, len(vectors)): если точек меньше,
    чем запрошено групп, каждая точка становится отдельной группой.
    Пустой вход — пустой список меток.

    Детерминизм между вызовами: инициализация и выбор сидов идут через
    ``numpy.random.default_rng(seed)`` с фиксированным значением по
    умолчанию — кэш до пересборки версии индекса (решение 3 ревью)
    дополняется этим свойством, а не заменяет его.
    """
    try:
        import numpy as np
    except ImportError as e:
        raise ImportError(
            "numpy не установлен. Установите orqion[graph]: pip install orqion[graph]"
        ) from e

    n = len(vectors)
    if n == 0:
        return []
    k = max(1, min(k, n))

    x = np.asarray([list(v) for v in vectors], dtype=np.float64)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    x = x / norms

    rng = np.random.default_rng(seed)
    centroids = _kmeans_pp_init(x, k, rng)

    labels = np.full(n, -1, dtype=np.int64)
    for _ in range(max_iterations):
        # Косинусная близость нормализованных векторов — скалярное произведение.
        new_labels = (x @ centroids.T).argmax(axis=1)
        if (new_labels == labels).all():
            break
        labels = new_labels
        for c in range(k):
            members = x[labels == c]
            if members.size == 0:
                continue
            centroid = members.mean(axis=0)
            norm = float(np.linalg.norm(centroid))
            if norm > 0.0:
                centroids[c] = centroid / norm

    return [int(v) for v in labels]


def _kmeans_pp_init(x: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """Инициализация k-means++ по косинусной дистанции (1 - близость)."""
    import numpy as np

    n = x.shape[0]
    first = int(rng.integers(n))
    centroids = [x[first]]
    # Лучшая близость каждой точки к уже выбранным центрам.
    closest_similarity = x @ x[first]
    for _ in range(1, k):
        distances = np.maximum(1.0 - closest_similarity, 0.0)
        total = float(distances.sum())
        if total <= 0.0:
            idx = int(rng.integers(n))
        else:
            idx = int(rng.choice(n, p=distances / total))
        centroids.append(x[idx])
        closest_similarity = np.maximum(closest_similarity, x @ x[idx])
    return np.stack(centroids)
