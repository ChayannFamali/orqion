"""CLI orqion: serve, migrate."""

from __future__ import annotations

import argparse
import asyncio

from app.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="orqion", description="orqion — LLM application")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Запустить сервер")
    serve_parser.add_argument("--host", default=None, help="Адрес привязки")
    serve_parser.add_argument("--port", type=int, default=None, help="Порт")

    subparsers.add_parser("migrate", help="Применить миграции")

    args = parser.parse_args()

    if args.command == "serve":
        _run_serve(args.host, args.port)
    elif args.command == "migrate":
        asyncio.run(_run_migrate())


def _run_serve(host: str | None, port: int | None) -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=host or settings.host,
        port=port or settings.port,
        log_level=settings.log_level.lower(),
    )


async def _run_migrate() -> None:
    from app.db.migrate import run_migrations

    settings = Settings()
    await run_migrations(settings)


if __name__ == "__main__":
    main()
