"""Перенос проектов из файлового хранилища (до P3) в базу метаданных.

До P3 документ проекта лежал в `projects/<id>/project.json`, история
отмены — в `history.json` рядом, корзина — каталогом `projects/.trash/<id>`
с `trashed.json`, копии перед миграцией схемы — `project.vN.bak`. Перенос
кладёт всё это в базу; файлы `project.json` и `history.json` не трогает,
поэтому откат на прежнюю версию приложения увидит проекты как раньше.

Проверка на копии (`dry_run`): каталог данных копируется во временный,
перенос и открытие каждого проекта выполняются там, а настоящие данные
остаются нетронутыми. Так видно, какие проекты не откроются, до того как
что-либо изменено.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from ..db import sqlite_url
from ..db.engine import aware
from ..project_models import InvalidStoredProjectError


@dataclass
class ImportReport:
    imported: list[str] = field(default_factory=list)
    trashed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "imported": self.imported,
            "trashed": self.trashed,
            "skipped": self.skipped,
            "failed": self.failed,
        }


def legacy_projects(root: Path) -> list[tuple[Path, bool]]:
    """Каталоги проектов старого формата: (каталог, лежит ли в корзине)."""
    found = [(path.parent, False) for path in sorted(root.glob("*/project.json"))]
    found += [(path.parent, True) for path in sorted((root / ".trash").glob("*/project.json"))]
    return [(directory, trashed) for directory, trashed in found if _is_uuid(directory.name)]


def import_legacy(repository, *, verify: bool = True) -> ImportReport:  # type: ignore[no-untyped-def]
    """Перенести проекты `repository.root` в базу `repository`.

    Уже перенесённые пропускаются, поэтому повторный запуск безопасен.
    `verify` — открыть каждый перенесённый проект обычным `get`, со всеми
    миграциями схемы и проверками; проект, который не открылся, удаляется
    из базы и попадает в `failed`, его файлы остаются как были.
    """
    report = ImportReport()
    root: Path = repository.root
    for directory, trashed in legacy_projects(root):
        identifier = directory.name
        if repository.metadata.exists(identifier):
            report.skipped.append(identifier)
            continue
        moved = False
        try:
            document = json.loads((directory / "project.json").read_text(encoding="utf-8"))
            if document.get("id") != identifier:
                raise InvalidStoredProjectError("id в project.json не совпадает с каталогом")
            target = root / identifier
            if trashed:
                if target.exists():
                    raise InvalidStoredProjectError("каталог есть и в библиотеке, и в корзине")
                os.replace(directory, target)
                moved = True
            repository.metadata.insert(document)
            stacks = _read_json(target / "history.json")
            if stacks:
                with repository.metadata.writing(identifier) as write:
                    write.update_history(
                        lambda _old, stacks=stacks: {
                            "undo": list(stacks.get("undo", [])),
                            "redo": list(stacks.get("redo", [])),
                        }
                    )
            for backup in sorted(target.glob("project.v*.bak")):
                version = backup.suffixes[-2].removeprefix(".v")
                if version.isdigit():
                    repository.metadata.keep_backup(
                        identifier,
                        int(version),
                        json.loads(backup.read_text(encoding="utf-8")),
                    )
            if trashed:
                marker = _read_json(target / "trashed.json") or {}
                moment = _parse(marker.get("trashed_at")) or datetime.now(UTC)
                repository.metadata.set_trashed(identifier, moment)
                report.trashed.append(identifier)
            elif verify:
                repository.get(UUID(identifier))
            report.imported.append(identifier)
        except Exception as exc:  # noqa: BLE001 — отчёт по каждому проекту
            if repository.metadata.exists(identifier) and identifier not in report.imported:
                repository.metadata.delete(identifier)
            if moved:
                os.replace(root / identifier, directory)
            report.failed[identifier] = f"{type(exc).__name__}: {exc}"
    return report


def dry_run(root: Path, max_upload_bytes: int) -> ImportReport:
    """Перенос на копии каталога проектов во временную базу SQLite."""
    from . import ProjectRepository

    with tempfile.TemporaryDirectory(prefix="sav-import-check-") as scratch:
        copy_root = Path(scratch) / "projects"
        shutil.copytree(root, copy_root, ignore=shutil.ignore_patterns("reports"))
        repository = ProjectRepository(
            copy_root, max_upload_bytes, sqlite_url(Path(scratch) / "check.db")
        )
        report = import_legacy(repository)
        repository.engine.dispose()
        return report


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _parse(value: object) -> datetime | None:
    try:
        return aware(datetime.fromisoformat(str(value))) if value else None
    except ValueError:
        return None


def _is_uuid(name: str) -> bool:
    try:
        UUID(name)
    except ValueError:
        return False
    return True
