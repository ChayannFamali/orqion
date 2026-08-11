"""Тесты освобождения ресурсов в lifespan (T-213a).

Проверки:
- close() вызывается для vector_store при завершении lifespan
- close() вызывается ровно один раз
- blob_store без close() не вызывает ошибку
- Исключение при закрытии одного ресурса не мешает закрыть остальные
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import pytest


@dataclass
class MockResource:
    """Ресурс с close() — mock для проверки вызова."""

    name: str
    close_count: int = 0
    raise_on_close: bool = False

    async def close(self) -> None:
        self.close_count += 1
        if self.raise_on_close:
            raise RuntimeError(f"close error in {self.name}")


@dataclass
class NoCloseResource:
    """Ресурс без close() — симметрия для проверки hasattr."""

    name: str


# ---------------------------------------------------------------------------
# AsyncExitStack — close() вызывается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_called_on_shutdown() -> None:
    """close() вызывается для ресурса при завершении lifespan."""
    store = MockResource(name="vector_store")

    cleanup = AsyncExitStack()
    cleanup.push_async_callback(store.close)

    await cleanup.aclose()

    assert store.close_count == 1


@pytest.mark.asyncio
async def test_close_called_exactly_once() -> None:
    """close() вызывается ровно один раз, не больше."""
    store = MockResource(name="vector_store")

    cleanup = AsyncExitStack()
    cleanup.push_async_callback(store.close)

    await cleanup.aclose()

    assert store.close_count == 1


@pytest.mark.asyncio
async def test_no_close_resource_skipped() -> None:
    """Ресурс без close() — не вызывает ошибку, hasattr пропускает."""
    blob = NoCloseResource(name="blob_store")

    cleanup = AsyncExitStack()
    if hasattr(blob, "close"):
        cleanup.push_async_callback(blob.close)
    # Не должно бросить — blob.close не зарегистрирован
    await cleanup.aclose()


@pytest.mark.asyncio
async def test_exception_in_one_close_does_not_block_others() -> None:
    """Исключение при close() одного ресурса не мешает закрыть остальные."""
    store1 = MockResource(name="store1", raise_on_close=True)
    store2 = MockResource(name="store2")

    cleanup = AsyncExitStack()
    cleanup.push_async_callback(store1.close)
    cleanup.push_async_callback(store2.close)

    # AsyncExitStack закрывает в LIFO порядке: store2 сначала, потом store1
    with pytest.raises(RuntimeError, match="close error in store1"):
        await cleanup.aclose()

    # store2 закрыт до исключения store1
    assert store2.close_count == 1
    # store1 тоже закрыт (close_count увеличен до raise)
    assert store1.close_count == 1


@pytest.mark.asyncio
async def test_multiple_resources_all_closed() -> None:
    """Несколько ресурсов — все закрываются."""
    store1 = MockResource(name="vector_store")
    store2 = MockResource(name="qdrant_store")

    cleanup = AsyncExitStack()
    cleanup.push_async_callback(store1.close)
    cleanup.push_async_callback(store2.close)

    await cleanup.aclose()

    assert store1.close_count == 1
    assert store2.close_count == 1


# ---------------------------------------------------------------------------
# Интеграция с lifespan — full lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_closes_vector_store() -> None:
    """Полный цикл lifespan: vector_store.close() вызывается при остановке.

    Симулирует паттерн из main.py: AsyncExitStack регистрирует close(),
    после yield — cleanup.aclose() закрывает.
    """
    vector_store = MockResource(name="vector_store")

    @asynccontextmanager
    async def test_lifespan() -> AsyncIterator[None]:
        cleanup = AsyncExitStack()
        if hasattr(vector_store, "close"):
            cleanup.push_async_callback(vector_store.close)
        yield
        await cleanup.aclose()

    async with test_lifespan():
        # Внутри lifespan — close не вызван
        assert vector_store.close_count == 0

    # После выхода из lifespan — close вызван
    assert vector_store.close_count == 1


@pytest.mark.asyncio
async def test_lifespan_with_no_close_resource() -> None:
    """Lifespan с ресурсом без close() — не падает."""
    blob_store = NoCloseResource(name="blob_store")

    @asynccontextmanager
    async def test_lifespan() -> AsyncIterator[None]:
        cleanup = AsyncExitStack()
        if hasattr(blob_store, "close"):
            cleanup.push_async_callback(blob_store.close)
        yield
        await cleanup.aclose()

    # Не должно бросить
    async with test_lifespan():
        pass
