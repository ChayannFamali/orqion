"""Заглушка провайдера: никогда не обращается в сеть."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class StubProvider:
    """Заглушка провайдера для тестов.

    Возвращает предопределённый ответ без сетевого вызова.
    """

    def __init__(self, response: str = "stub-response") -> None:
        self._response = response
        self.call_count = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str = "stub-model",
        **kwargs: Any,
    ) -> str:
        self.call_count += 1
        return self._response

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str = "stub-model",
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        self.call_count += 1
        for word in self._response.split():
            yield word + " "


def stub_provider() -> StubProvider:
    """Фикстура: возвращает заглушку провайдера."""
    return StubProvider()
