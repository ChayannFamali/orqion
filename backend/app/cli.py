"""CLI orqion: serve, migrate, createuser, reset-password."""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="orqion", description="orqion — LLM application")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Запустить сервер")
    serve_parser.add_argument("--host", default=None, help="Адрес привязки")
    serve_parser.add_argument("--port", type=int, default=None, help="Порт")

    subparsers.add_parser("migrate", help="Применить миграции")

    create_parser = subparsers.add_parser("createuser", help="Создать пользователя")
    create_parser.add_argument("--email", required=True, help="Email пользователя")
    create_parser.add_argument("--role", default="developer", help="Роль (default: developer)")
    create_parser.add_argument(
        "--password",
        default=None,
        help="Пароль. Если не указан — генерируется и выводится в stdout.",
    )

    reset_parser = subparsers.add_parser("reset-password", help="Сбросить пароль пользователя")
    reset_parser.add_argument("--email", required=True, help="Email пользователя")
    reset_parser.add_argument(
        "--password",
        default=None,
        help="Новый пароль. Если не указан — генерируется и выводится в stdout.",
    )

    args = parser.parse_args()

    if args.command == "serve":
        _run_serve(args.host, args.port)
    elif args.command == "migrate":
        asyncio.run(_run_migrate())
    elif args.command == "createuser":
        asyncio.run(_run_createuser(args.email, args.role, args.password))
    elif args.command == "reset-password":
        asyncio.run(_run_reset_password(args.email, args.password))


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


async def _run_createuser(email: str, role_name: str, password: str | None) -> None:
    """Создаёт пользователя с указанной ролью.

    Пароль: если не указан — генерируется и выводится в stdout.
    Пароль не попадает в логи (AGENTS.md §14).
    """
    import secrets

    from sqlalchemy import select

    from app.auth.bootstrap import ensure_builtin_roles
    from app.auth.passwords import hash_password
    from app.db.engine import create_engine, create_session_factory
    from app.db.models import Role, User
    from app.db.workspace import ensure_default_workspace

    settings = Settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    generated = False
    if password is None:
        password = secrets.token_urlsafe(16)
        generated = True

    async with session_factory() as session:
        workspace_id = await ensure_default_workspace(session)
        await ensure_builtin_roles(session, workspace_id)

        existing = await session.execute(
            select(User).where(
                User.workspace_id == workspace_id,
                User.email == email,
            )
        )
        if existing.scalar_one_or_none() is not None:
            print(f"Error: user '{email}' already exists", file=sys.stderr, flush=True)
            await engine.dispose()
            sys.exit(1)

        role_result = await session.execute(
            select(Role).where(
                Role.workspace_id == workspace_id,
                Role.name == role_name,
            )
        )
        role = role_result.scalar_one_or_none()
        if role is None:
            print(f"Error: role '{role_name}' not found", file=sys.stderr, flush=True)
            await engine.dispose()
            sys.exit(1)

        user = User(
            workspace_id=workspace_id,
            email=email,
            password_hash=hash_password(password),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()

    if generated:
        print(
            f"\n=== orqion: user created ===\n"
            f"Email: {email}\n"
            f"Role: {role_name}\n"
            f"Password: {password}\n"
            f"=== Save this password. It will not be shown again. ===\n",
            file=sys.stdout,
            flush=True,
        )
    else:
        print(f"User created: {email} ({role_name})", file=sys.stdout, flush=True)

    await engine.dispose()


async def _run_reset_password(email: str, password: str | None) -> None:
    """Сбрасывает пароль пользователя.

    Пароль: если не указан — генерируется и выводится в stdout.
    """
    import secrets

    from sqlalchemy import select

    from app.auth.passwords import hash_password
    from app.db.engine import create_engine, create_session_factory
    from app.db.models import Session as SessionModel
    from app.db.models import User

    settings = Settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    generated = False
    if password is None:
        password = secrets.token_urlsafe(16)
        generated = True

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            print(f"Error: user '{email}' not found", file=sys.stderr, flush=True)
            await engine.dispose()
            sys.exit(1)

        user.password_hash = hash_password(password)

        # Отозвать все активные сессии — скомпрометированный аккаунт не остаётся в системе
        from sqlalchemy import delete

        await session.execute(delete(SessionModel).where(SessionModel.user_id == user.id))
        await session.commit()

    if generated:
        print(
            f"\n=== orqion: password reset ===\n"
            f"Email: {email}\n"
            f"Password: {password}\n"
            f"=== Save this password. It will not be shown again. ===\n",
            file=sys.stdout,
            flush=True,
        )
    else:
        print(f"Password reset for: {email}", file=sys.stdout, flush=True)

    await engine.dispose()


if __name__ == "__main__":
    main()
