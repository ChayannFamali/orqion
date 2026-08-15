"""ASGI in-process diagnostic для BUG-007: тот же app, тот же engine, тот же pool,
но через httpx.ASGITransport (без uvicorn HTTP layer).

Если ASGI in-process даёт 0% ошибок при concurrency=10, а uvicorn даёт >0% —
проблема в uvicorn HTTP layer (подтверждает диагноз BUG-007).

Если ASGI in-process тоже даёт >0% — проблема в application/DB layer.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx


async def main() -> None:
    concurrent = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    from app.config import Settings, get_or_create_secret_key
    from app.main import create_app

    settings = Settings()
    if not settings.secret_key:
        from pathlib import Path
        data_dir = Path(settings.blob_store_path).parent
        data_dir.mkdir(parents=True, exist_ok=True)
        settings.secret_key = get_or_create_secret_key(settings, data_dir)

    # Use the existing database (already migrated + has admin)
    app = create_app()

    # Start the app lifespan
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        errors = 0
        error_details: list[str] = []

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Login
            resp = await client.post(
                "/api/auth/login",
                json={"email": "admin@orqion.local", "password": os.environ.get("ORQION_ADMIN_PASSWORD", "")},
            )
            if resp.status_code != 200:
                # Try without password (session might already be set up)
                print(f"Login failed: {resp.status_code} {resp.text}")
                return

            cookies = resp.cookies

            async def worker() -> None:
                nonlocal errors
                for _ in range(total // concurrent):
                    try:
                        r = await client.post(
                            "/api/chat",
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
                            if len(error_details) < 5:
                                error_details.append(f"status={r.status_code} body={r.text[:200]!r}")
                    except Exception as e:  # noqa: BLE001
                        errors += 1
                        if len(error_details) < 5:
                            error_details.append(f"exception: {type(e).__name__}: {e}")

            tasks = [asyncio.create_task(worker()) for _ in range(concurrent)]
            await asyncio.gather(*tasks)

        print(f"ASGI in-process: {total} requests, {errors} errors ({errors / total * 100:.1f}%)")
        if error_details:
            print("Error details:")
            for d in error_details:
                print(f"  {d}")


if __name__ == "__main__":
    asyncio.run(main())
