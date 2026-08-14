"""Нагрузочный скрипт для orqion (T-410).

Простой asyncio-скрипт: N concurrent chat-запросов к запущенному orqion.
Фиксирует latency (avg/p95/p99) и error rate.

Ручная верификация, не CI-гейт. По прецеденту T-202 — реальный прогон
с зафиксированными числами для документации.

Usage:
    python backend/scripts/load_test.py --url http://localhost:8000 --concurrent 10 \
        --email admin@test.local --password test123 --model test-model
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


async def _login(
    client: httpx.AsyncClient,
    url: str,
    email: str,
    password: str,
) -> None:
    """Логин и сохранение session cookie в client."""
    resp = await client.post(
        f"{url}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed: {resp.status_code} {resp.text}")


async def single_request(
    client: httpx.AsyncClient,
    url: str,
    model: str,
    prompt: str,
    error_types: dict[str, int] | None = None,
) -> tuple[float, int]:
    """Отправляет один chat-запрос. Возвращает (latency_ms, status_code).

    При ошибке записывает тип в error_types (если передан).
    """
    start = time.perf_counter()
    try:
        resp = await client.post(
            f"{url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=30.0,
        )
        elapsed = (time.perf_counter() - start) * 1000
        if resp.status_code != 200 and error_types is not None:
            error_types[f"http_{resp.status_code}"] = (
                error_types.get(f"http_{resp.status_code}", 0) + 1
            )
        return elapsed, resp.status_code
    except httpx.TimeoutException:
        elapsed = (time.perf_counter() - start) * 1000
        if error_types is not None:
            error_types["timeout"] = error_types.get("timeout", 0) + 1
        return elapsed, 0
    except httpx.ConnectError as e:
        elapsed = (time.perf_counter() - start) * 1000
        if error_types is not None:
            error_types[f"connect_error: {type(e).__name__}"] = (
                error_types.get(f"connect_error: {type(e).__name__}", 0) + 1
            )
        return elapsed, 0
    except httpx.HTTPError as e:
        elapsed = (time.perf_counter() - start) * 1000
        if error_types is not None:
            error_types[f"http_error: {type(e).__name__}"] = (
                error_types.get(f"http_error: {type(e).__name__}", 0) + 1
            )
        return elapsed, 0


async def run_load_test(
    url: str,
    concurrent: int,
    total_requests: int,
    model: str,
    prompt: str,
    client: httpx.AsyncClient | None = None,
    email: str | None = None,
    password: str | None = None,
) -> dict[str, float | int]:
    """Запускает нагрузочный тест. Возвращает метрики.

    Если client передан (для тестов) — используется он.
    Иначе — создаётся новый httpx.AsyncClient.
    Если email/password переданы — выполняется login для получения session cookie.
    """
    latencies: list[float] = []
    errors = 0
    completed = 0
    error_types: dict[str, int] = {}

    async def _run(c: httpx.AsyncClient) -> None:
        nonlocal errors, completed
        semaphore = asyncio.Semaphore(concurrent)

        async def worker() -> None:
            nonlocal errors, completed
            for _ in range(total_requests // concurrent):
                async with semaphore:
                    latency, status = await single_request(c, url, model, prompt, error_types)
                    latencies.append(latency)
                    if status != 200:
                        errors += 1
                    completed += 1

        tasks = [asyncio.create_task(worker()) for _ in range(concurrent)]
        await asyncio.gather(*tasks)

    if client is not None:
        await _run(client)
    else:
        async with httpx.AsyncClient() as c:
            if email and password:
                await _login(c, url, email, password)
            await _run(c)

    latencies.sort()
    n = len(latencies)

    def percentile(p: float) -> float:
        if not latencies:
            return 0.0
        idx = max(0, min(n - 1, int(n * p) - 1))
        return latencies[idx]

    return {
        "total_requests": completed,
        "errors": errors,
        "error_rate": (errors / completed * 100) if completed else 0.0,
        "error_types": error_types,
        "avg_latency_ms": statistics.mean(latencies) if latencies else 0.0,
        "p50_latency_ms": percentile(0.50),
        "p95_latency_ms": percentile(0.95),
        "p99_latency_ms": percentile(0.99),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Нагрузочный тест orqion")
    parser.add_argument("--url", default="http://localhost:8000", help="URL orqion")
    parser.add_argument("--concurrent", type=int, default=10, help="Concurrent requests")
    parser.add_argument("--total", type=int, default=100, help="Total requests")
    parser.add_argument("--model", default="test-model", help="Model alias")
    parser.add_argument("--prompt", default="Hello, how are you?", help="Prompt text")
    parser.add_argument("--email", default=None, help="Login email for auth")
    parser.add_argument("--password", default=None, help="Login password for auth")
    args = parser.parse_args()

    print(f"Нагрузочный тест: {args.url}")
    print(f"  Concurrent: {args.concurrent}")
    print(f"  Total: {args.total}")
    print(f"  Model: {args.model}")
    if args.email:
        print(f"  Auth: {args.email}")
    print()

    results = asyncio.run(
        run_load_test(
            args.url,
            args.concurrent,
            args.total,
            args.model,
            args.prompt,
            email=args.email,
            password=args.password,
        )
    )

    print("Результаты:")
    print(f"  Requests:    {results['total_requests']}")
    print(f"  Errors:      {results['errors']} ({results['error_rate']:.1f}%)")
    print(f"  Avg latency: {results['avg_latency_ms']:.1f} ms")
    print(f"  P50 latency: {results['p50_latency_ms']:.1f} ms")
    print(f"  P95 latency: {results['p95_latency_ms']:.1f} ms")
    print(f"  P99 latency: {results['p99_latency_ms']:.1f} ms")

    error_types: dict[str, int] = results.get("error_types", {})  # type: ignore[assignment]
    if error_types:
        print("  Error breakdown:")
        for etype, count in sorted(error_types.items()):
            print(f"    {etype}: {count}")

    if results["errors"] > 0:
        print(f"\nВНИМАНИЕ: {results['errors']} ошибок из {results['total_requests']}")


if __name__ == "__main__":
    main()
