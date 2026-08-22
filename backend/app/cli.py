"""CLI orqion: serve, migrate, createuser, reset-password, ingest-git, export-config, import-config."""

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

    git_parser = subparsers.add_parser(
        "ingest-git",
        help="Индексировать git-репозиторий",
    )
    git_parser.add_argument("url", help="URL git-репозитория или локальный путь")
    git_parser.add_argument(
        "--corpus",
        default=None,
        help="Имя корпуса. Если не указано — выводится из URL.",
    )
    git_parser.add_argument(
        "--extensions",
        default=None,
        help="Список расширений через запятую (по умолчанию: .py,.ts,.go,...)",
    )
    git_parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Глубина clone (default: 1 = shallow). 0 = полная история.",
    )
    git_parser.add_argument(
        "--clone-timeout",
        type=int,
        default=120,
        help="Таймаут clone в секундах (default: 120)",
    )
    git_parser.add_argument(
        "--max-clone-size",
        type=int,
        default=500,
        help="Максимальный размер клона в MB (default: 500)",
    )
    git_parser.add_argument(
        "--max-file-size",
        type=int,
        default=50,
        help="Максимальный размер одного файла в MB (default: 50)",
    )
    git_parser.add_argument(
        "--build-index",
        action="store_true",
        help="Построить индекс после загрузки документов",
    )

    export_parser = subparsers.add_parser(
        "export-config",
        help="Экспорт ролей и routing rules в YAML",
    )
    export_parser.add_argument(
        "--output",
        default=None,
        help="Файл для записи YAML. Если не указан — stdout.",
    )

    import_parser = subparsers.add_parser(
        "import-config",
        help="Импорт ролей и routing rules из YAML",
    )
    import_parser.add_argument(
        "--input",
        default=None,
        help="Файл YAML для импорта. Если не указан — stdin.",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать изменения без записи в БД.",
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
    elif args.command == "ingest-git":
        asyncio.run(
            _run_ingest_git(
                url=args.url,
                corpus_name=args.corpus,
                extensions_str=args.extensions,
                depth=args.depth,
                clone_timeout=args.clone_timeout,
                max_clone_size=args.max_clone_size,
                max_file_size=args.max_file_size,
                build_index=args.build_index,
            )
        )
    elif args.command == "export-config":
        asyncio.run(_run_export_config(output_path=args.output))
    elif args.command == "import-config":
        asyncio.run(_run_import_config(input_path=args.input, dry_run=args.dry_run))


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


async def _run_ingest_git(
    *,
    url: str,
    corpus_name: str | None,
    extensions_str: str | None,
    depth: int,
    clone_timeout: int,
    max_clone_size: int,
    max_file_size: int,
    build_index: bool,
) -> None:
    """Индексирует git-репозиторий: clone → upload_document → (опц.) build_index."""
    import re

    from sqlalchemy import select

    from app.db.engine import create_engine, create_session_factory
    from app.db.models import Corpus
    from app.db.workspace import ensure_default_workspace
    from app.rag.blob import LocalBlobStore
    from app.rag.git_ingest import DEFAULT_EXTENSIONS, ingest_git_repository

    settings = Settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    blob_store = LocalBlobStore(settings.blob_store_path)

    # Имя корпуса из URL: https://github.com/user/repo → repo
    if corpus_name is None:
        name = url.rstrip("/").split("/")[-1]
        name = re.sub(r"\.git$", "", name)
        corpus_name = name or "git-import"

    # Парсинг расширений
    if extensions_str is not None:
        extensions = [e.strip() for e in extensions_str.split(",") if e.strip()]
    else:
        extensions = list(DEFAULT_EXTENSIONS)

    max_file_size_bytes = max_file_size * 1024 * 1024

    print(f"Cloning: {url} (depth={depth})", flush=True)

    async with session_factory() as session:
        workspace_id = await ensure_default_workspace(session)

        # Поиск или создание корпуса
        corpus_result = await session.execute(
            select(Corpus).where(
                Corpus.workspace_id == workspace_id,
                Corpus.name == corpus_name,
            )
        )
        corpus = corpus_result.scalar_one_or_none()
        if corpus is None:
            corpus = Corpus(
                workspace_id=workspace_id,
                name=corpus_name,
            )
            session.add(corpus)
            await session.flush()
            print(f"Created corpus: {corpus_name} ({corpus.id})", flush=True)
        else:
            print(f"Using existing corpus: {corpus_name} ({corpus.id})", flush=True)

        ingest_result = await ingest_git_repository(
            session,
            blob_store,
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            repo_url=url,
            allowed_extensions=extensions,
            max_file_size_bytes=max_file_size_bytes,
            depth=depth,
            clone_timeout_seconds=clone_timeout,
            max_clone_size_mb=max_clone_size,
        )

        print(
            f"\n=== orqion: git ingestion complete ===\n"
            f"Repository: {url}\n"
            f"Corpus: {corpus_name}\n"
            f"Total files: {ingest_result.total_files}\n"
            f"Ingested: {ingest_result.ingested}\n"
            f"Skipped (duplicates): {ingest_result.skipped}\n"
            f"Failed: {ingest_result.failed}\n"
            + (
                "Errors:\n" + "\n".join(f"  {e}" for e in ingest_result.errors) + "\n"
                if ingest_result.errors
                else ""
            )
            + "=== Done ===\n",
            file=sys.stdout,
            flush=True,
        )

        if build_index and ingest_result.ingested > 0:
            from pathlib import Path

            from app.config import get_or_create_secret_key
            from app.rag.embedding_resolver import resolve_embedding_backend
            from app.rag.index_builder import build_index_version
            from app.rag.vector_store import SQLiteVectorStore

            data_dir = Path(settings.blob_store_path).parent
            secret_key = get_or_create_secret_key(settings, data_dir)

            vector_store = SQLiteVectorStore(settings.vector_store_path)
            embedding_backend = await resolve_embedding_backend(
                settings, session, workspace_id, secret_key
            )
            print("Building index...", flush=True)
            build_result = await build_index_version(
                session,
                blob_store,
                vector_store,
                embedding_backend,
                workspace_id=workspace_id,
                corpus_id=corpus.id,
            )
            await vector_store.close()
            print(
                f"Index built: {build_result.chunks_created} chunks, "
                f"{build_result.documents_processed} documents processed",
                flush=True,
            )

    await engine.dispose()


async def _run_export_config(*, output_path: str | None) -> None:
    """Экспортирует роли и routing rules в YAML."""
    from app.config_io.service import export_config
    from app.db.engine import create_engine, create_session_factory
    from app.db.workspace import ensure_default_workspace

    settings = Settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        workspace_id = await ensure_default_workspace(session)
        yaml_content = await export_config(session, workspace_id)

    if output_path is not None:
        from pathlib import Path

        Path(output_path).write_text(yaml_content, encoding="utf-8")
        print(f"Config exported to: {output_path}", file=sys.stdout, flush=True)
    else:
        print(yaml_content, end="", file=sys.stdout, flush=True)

    await engine.dispose()


async def _run_import_config(*, input_path: str | None, dry_run: bool) -> None:
    """Импортирует роли и routing rules из YAML."""
    from app.config_io.service import import_config
    from app.db.engine import create_engine, create_session_factory
    from app.db.workspace import ensure_default_workspace
    from app.errors import OrqionError

    if input_path is not None:
        from pathlib import Path

        yaml_content = Path(input_path).read_text(encoding="utf-8")
    else:
        yaml_content = sys.stdin.read()

    settings = Settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            workspace_id = await ensure_default_workspace(session)
            result = await import_config(
                session,
                workspace_id,
                yaml_content,
                dry_run=dry_run,
            )
            if not dry_run:
                await session.commit()

        prefix = "[DRY RUN] " if dry_run else ""
        print(
            f"\n=== orqion: config import {prefix}===\n"
            f"Roles created: {result.roles_created}\n"
            f"Roles updated: {result.roles_updated}\n"
            f"Roles unchanged: {result.roles_unchanged}\n"
            f"Routing rules replaced: {result.routing_rules_replaced}\n"
            f"Routing rules count: {result.routing_rules_count}\n"
            + (
                "Warnings:\n" + "\n".join(f"  - {w}" for w in result.warnings) + "\n"
                if result.warnings
                else ""
            )
            + "=== Done ===\n",
            file=sys.stdout,
            flush=True,
        )
    except OrqionError as exc:
        print(f"Error: {exc.reason}", file=sys.stderr, flush=True)
        if exc.hint:
            print(f"Hint: {exc.hint}", file=sys.stderr, flush=True)
        await engine.dispose()
        sys.exit(1)

    await engine.dispose()


if __name__ == "__main__":
    main()
