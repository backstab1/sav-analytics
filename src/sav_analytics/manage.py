"""Служебные команды: `sav-analytics <команда>` или `python -m sav_analytics.manage`.

- `migrate` — привести схему базы к версии приложения;
- `import-legacy [--dry-run]` — перенести проекты из файлового хранилища;
- `purge-trash` — окончательно удалить проекты, пролежавшие в корзине
  дольше `SAV_ANALYTICS_TRASH_RETENTION_DAYS`;
- `create-user <имя> [--admin]`, `set-password <имя>`, `list-users` —
  учётные записи (P4). Пароль спрашивается в терминале или берётся из
  `SAV_ANALYTICS_NEW_PASSWORD` для скриптов развёртывания.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Sequence

from .settings import Settings


def _repository(settings: Settings, *, migrate: bool = True):  # type: ignore[no-untyped-def]
    from .repository import ProjectRepository

    return ProjectRepository(
        settings.projects_dir,
        settings.max_upload_bytes,
        settings.resolved_database_url,
        migrate=migrate,
    )


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_migrate(settings: Settings, _args: argparse.Namespace) -> int:
    from .db.engine import _create_engine, current_revision, head_revision, run_migrations

    engine = _create_engine(settings.resolved_database_url)
    before = current_revision(engine)
    run_migrations(engine)
    _print({"from": before, "to": head_revision()})
    engine.dispose()
    return 0


def cmd_import_legacy(settings: Settings, args: argparse.Namespace) -> int:
    from .repository.legacy_import import dry_run, import_legacy

    if args.dry_run:
        report = dry_run(settings.projects_dir, settings.max_upload_bytes)
    else:
        report = import_legacy(_repository(settings))
    _print({"dry_run": args.dry_run, **report.as_dict()})
    return 1 if report.failed else 0


def cmd_purge_trash(settings: Settings, _args: argparse.Namespace) -> int:
    purged = _repository(settings).purge_expired(settings.trash_retention_days)
    _print({"retention_days": settings.trash_retention_days, "purged": purged})
    return 0


def _auth(settings: Settings):  # type: ignore[no-untyped-def]
    from .auth.store import AuthStore
    from .db import get_engine

    return AuthStore(get_engine(settings.resolved_database_url, migrate=settings.auto_migrate))


def _password() -> str:
    supplied = os.environ.get("SAV_ANALYTICS_NEW_PASSWORD")
    if supplied:
        return supplied
    first = getpass.getpass("Пароль: ")
    if first != getpass.getpass("Ещё раз: "):
        raise SystemExit("Пароли не совпали.")
    return first


def cmd_create_user(settings: Settings, args: argparse.Namespace) -> int:
    from .auth.store import AuthError

    try:
        user = _auth(settings).create_user(
            args.username, _password(),
            role="admin" if args.admin else "user", display_name=args.display_name or "",
        )
    except AuthError as exc:
        raise SystemExit(str(exc)) from exc
    _print(user)
    return 0


def cmd_set_password(settings: Settings, args: argparse.Namespace) -> int:
    from .auth.store import AuthError, normalize_username

    store = _auth(settings)
    name = normalize_username(args.username)
    user = next((item for item in store.list_users() if item["username"] == name), None)
    if user is None:
        raise SystemExit("Пользователь не найден.")
    try:
        store.update_user(user["id"], password=_password(), active=True)
    except AuthError as exc:
        raise SystemExit(str(exc)) from exc
    _print({"username": name, "status": "password_set"})
    return 0


def cmd_list_users(settings: Settings, _args: argparse.Namespace) -> int:
    _print(_auth(settings).list_users())
    return 0


COMMANDS = {
    "migrate": (cmd_migrate, "привести схему базы к версии приложения"),
    "import-legacy": (cmd_import_legacy, "перенести проекты из файлового хранилища"),
    "purge-trash": (cmd_purge_trash, "удалить проекты с истёкшим сроком в корзине"),
    "create-user": (cmd_create_user, "завести пользователя"),
    "set-password": (cmd_set_password, "задать пароль пользователю"),
    "list-users": (cmd_list_users, "список пользователей"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sav-analytics")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, (_handler, help_text) in COMMANDS.items():
        command = commands.add_parser(name, help=help_text)
        if name == "import-legacy":
            command.add_argument(
                "--dry-run",
                action="store_true",
                help="проверить перенос на копии данных, ничего не меняя",
            )
        if name in ("create-user", "set-password"):
            command.add_argument("username")
        if name == "create-user":
            command.add_argument("--admin", action="store_true", help="роль администратора")
            command.add_argument("--display-name", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler, _help = COMMANDS[args.command]
    return handler(Settings(), args)


if __name__ == "__main__":
    sys.exit(main())
