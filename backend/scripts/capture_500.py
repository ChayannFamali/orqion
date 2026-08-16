"""Быстрый скрипт для захвата тела 500-х ответов при нагрузке.

Отправляет N concurrent запросов, при non-200 сохраняет тело ответа.
Используется для диагностики BUG-007 на Linux.
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx


async def main() -> None:
    url = "http://localhost:8000"
    email = "admin@orqion.local"
    password = os.environ.get("ORQION_ADMIN_PASSWORD", "HsV-AFvlfADwZiJtOQqGHg")
    concurrent = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 200

    async with httpx.AsyncClient() as client:
        cookies = None

        # Проверяем --cookie-file (переданные curl cookies) — пропускаем логин
        cookie_file = None
        args = sys.argv[3:]
        if "--cookie-file" in args:
            idx = args.index("--cookie-file")
            cookie_file = args[idx + 1]

        if cookie_file:
            # Загружаем cookies из файла (Netscape format от curl -c)
            cookies = httpx.Cookies()
            with open(cookie_file) as f:  # noqa: ASYNC230
                for line in f:
                    if line.startswith("#") and not line.startswith("#HttpOnly_"):
                        continue
                    if not line.strip():
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) >= 7:
                        domain = parts[0].removeprefix("#HttpOnly_")
                        cookies.set(parts[5], parts[6], domain=domain, path=parts[2])
        else:
            # Login
            resp = await client.post(
                f"{url}/api/auth/login",
                json={"email": email, "password": password},
                timeout=10.0,
            )
            if resp.status_code != 200:
                print(f"Login failed: {resp.status_code} {resp.text}")
                return
            cookies = resp.cookies

        errors = 0
        error_bodies: dict[str, list[int]] = {}
        semaphore = asyncio.Semaphore(concurrent)

        async def worker(idx: int) -> None:
            nonlocal errors
            async with semaphore:
                try:
                    r = await client.post(
                        f"{url}/api/chat",
                        json={
                            "model": "test-model",
                            "messages": [{"role": "user", "content": "Hello"}],
                            "stream": False,
                        },
                        cookies=cookies,
                        timeout=30.0,
                    )
                    if r.status_code != 200:
                        errors += 1
                        body_preview = r.text[:4000]
                        key = f"http_{r.status_code}"
                        error_bodies.setdefault(key, [])
                        error_bodies[key].append(idx)
                        if len(error_bodies[key]) <= 3:
                            print(f"  [req #{idx}] {key}: body={body_preview!r}")
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    key = f"exception:{type(e).__name__}"
                    error_bodies.setdefault(key, [])
                    error_bodies[key].append(idx)
                    if len(error_bodies[key]) <= 3:
                        print(f"  [req #{idx}] {key}: {e}")

        tasks = [asyncio.create_task(worker(i)) for i in range(total)]
        await asyncio.gather(*tasks)

        print(f"\nResults: {total} requests, {errors} errors ({errors / total * 100:.1f}%)")
        if error_bodies:
            print("Error breakdown:")
            for key, indices in sorted(error_bodies.items()):
                print(f"  {key}: {len(indices)} occurrences (first at req #{indices[0]})")


if __name__ == "__main__":
    asyncio.run(main())
