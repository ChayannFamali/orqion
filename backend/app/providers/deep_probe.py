"""Измерение фактического контекста модели (observed_context).

Ручная операция, запускается администратором. Не встроена в плановый ре-probe (T-112a) —
отправка длинного промпта дорога на медленных локальных моделях.
Бинарный поиск по размеру промпта, максимум 4 попытки.
"""

from __future__ import annotations

from app.db.models import Model, Provider
from app.providers.client import ProviderClient

MAX_ATTEMPTS = 4
PROBE_TEMPERATURE = 0.0
PROBE_MAX_TOKENS = 1


async def measure_observed_context(
    provider: Provider,
    model: Model,
    secret_key: str,
) -> int | None:
    """Измеряет фактически загруженный размер контекста модели.

    Возвращает observed_context (int) или None при невозможности измерить.
    Бинарный поиск: начинает с model.max_input_tokens (или 4096 по умолчанию),
    сужает до отказа или успеха.
    Максимум 4 попытки — каждая отправка реального промпта дорога.
    """
    if model.upstream_name is None:
        return None

    upper = model.max_input_tokens or 4096
    lower = 256
    result: int | None = None

    client = ProviderClient(provider, secret_key, timeout=60.0)

    for _ in range(MAX_ATTEMPTS):
        mid = (lower + upper) // 2
        success = await _try_context(client, model.upstream_name, mid)

        if success:
            result = mid
            lower = mid + 1
        else:
            upper = mid - 1

        if lower > upper:
            break

    return result


async def _try_context(
    client: ProviderClient,
    model: str,
    token_estimate: int,
) -> bool:
    """Отправляет промпт размером ~token_estimate токенов.

    True — модель приняла запрос. False — отказ (context length exceeded).
    """
    prompt = _make_prompt(token_estimate)
    try:
        await client.complete(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            max_tokens=PROBE_MAX_TOKENS,
            temperature=PROBE_TEMPERATURE,
        )
        return True
    except Exception:  # noqa: BLE001 — probe не должен падать
        return False


def _make_prompt(token_estimate: int) -> str:
    """Создаёт промпт приблизительно token_estimate токенов.

    Грубая оценка: ~4 символа на токен для английского текста.
    Заполняет повторяющимся текстом — содержимое не важно, важен размер.
    """
    char_count = token_estimate * 4
    filler = "The quick brown fox jumps over the lazy dog. "
    repetitions = max(1, char_count // len(filler))
    return filler * repetitions
