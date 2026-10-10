"""Документы проектов в базе: чтение, атомарная запись ревизией, корзина.

Ревизия проверяется самой базой: `UPDATE ... WHERE revision = :expected`.
Блокировка внутри процесса защищала только от соседних потоков; два
экземпляра API за балансировщиком теряли бы правку друг друга. Теперь
проигравшая запись получает конфликт, где бы она ни выполнялась.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from ..db.engine import aware, utcnow
from ..db.schema import project_backups, project_history, projects

EMPTY_HISTORY: dict[str, list[dict[str, Any]]] = {"undo": [], "redo": []}


class RevisionMismatchError(RuntimeError):
    """В базе другая ревизия: проект изменили между чтением и записью."""


def encode(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False)


def decode(text: str) -> dict[str, Any]:
    return json.loads(text)


class ProjectMetadata:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # Чтение -----------------------------------------------------------------

    def load(self, project_id: str, *, trashed: bool = False) -> dict[str, Any] | None:
        """Документ проекта; проект в корзине — только при `trashed=True`."""
        query = sa.select(projects.c.document, projects.c.trashed_at).where(
            projects.c.id == project_id
        )
        with self.engine.connect() as connection:
            row = connection.execute(query).first()
        if row is None or (row.trashed_at is not None) != trashed:
            return None
        return decode(row.document)

    def exists(self, project_id: str) -> bool:
        query = sa.select(projects.c.id).where(projects.c.id == project_id)
        with self.engine.connect() as connection:
            return connection.execute(query).first() is not None

    def summaries(self, *, trashed: bool = False) -> list[dict[str, Any]]:
        condition = (
            projects.c.trashed_at.is_not(None) if trashed else projects.c.trashed_at.is_(None)
        )
        query = sa.select(
            projects.c.id,
            projects.c.name,
            projects.c.created_at,
            projects.c.original_filename,
            projects.c.trashed_at,
            projects.c.document,
        ).where(condition)
        with self.engine.connect() as connection:
            rows = connection.execute(query).all()
        items = []
        for row in rows:
            # Время создания — из документа, как его писали всегда: строкой
            # с тем же форматом, который видит клиент.
            document = decode(row.document)
            item = {
                "id": row.id,
                "name": row.name,
                "created_at": document.get("created_at"),
                "original_filename": row.original_filename,
            }
            if trashed:
                item["trashed_at"] = _iso(row.trashed_at)
            items.append(item)
        key = "trashed_at" if trashed else "created_at"
        return sorted(items, key=lambda item: item[key] or "", reverse=True)

    def trashed_before(self, moment: datetime) -> list[str]:
        query = sa.select(projects.c.id).where(projects.c.trashed_at < moment)
        with self.engine.connect() as connection:
            return [row.id for row in connection.execute(query)]

    # Запись -----------------------------------------------------------------

    def insert(self, document: dict[str, Any], *, created_by: str | None = None) -> None:
        now = utcnow()
        with self.engine.begin() as connection:
            connection.execute(
                projects.insert().values(
                    id=document["id"],
                    name=document["name"],
                    original_filename=document.get("original_filename", ""),
                    created_at=_parse(document.get("created_at")) or now,
                    updated_at=now,
                    revision=int(document["configuration"].get("revision", 1)),
                    document=encode(document),
                    created_by=created_by,
                )
            )

    @contextmanager
    def writing(self, project_id: str) -> Iterator[ProjectWrite]:
        """Транзакция записи одного проекта: прочитать, проверить, записать."""
        with self.engine.begin() as connection:
            yield ProjectWrite(connection, project_id)

    def overwrite(self, project_id: str, document: dict[str, Any]) -> None:
        """Записать документ как есть, мимо ревизии и истории.

        Для мигратора файлового хранилища и тестов, которые имитируют проект,
        сохранённый старой версией приложения.
        """
        with self.engine.begin() as connection:
            connection.execute(
                projects.update()
                .where(projects.c.id == project_id)
                .values(
                    document=encode(document),
                    name=document["name"],
                    revision=int(document.get("configuration", {}).get("revision", 1)),
                    updated_at=utcnow(),
                )
            )

    def set_trashed(self, project_id: str, moment: datetime | None) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                projects.update()
                .where(projects.c.id == project_id)
                .where(
                    projects.c.trashed_at.is_(None)
                    if moment is not None
                    else projects.c.trashed_at.is_not(None)
                )
                .values(trashed_at=moment)
            )
        return result.rowcount == 1

    def delete(self, project_id: str) -> None:
        with self.engine.begin() as connection:
            # Внешние ключи SQLite включены, но каскад пишем явно: так же
            # работает и база, созданная без них.
            for table in (project_history, project_backups):
                connection.execute(table.delete().where(table.c.project_id == project_id))
            connection.execute(projects.delete().where(projects.c.id == project_id))

    # Копии перед миграцией схемы ---------------------------------------------

    def keep_backup(self, project_id: str, schema_version: int, document: dict) -> None:
        """Копия документа прежней схемы; вторая для той же схемы не пишется."""
        with self.engine.begin() as connection:
            exists = connection.execute(
                sa.select(project_backups.c.id)
                .where(project_backups.c.project_id == project_id)
                .where(project_backups.c.schema_version == schema_version)
            ).first()
            if exists is None:
                connection.execute(
                    project_backups.insert().values(
                        project_id=project_id,
                        schema_version=schema_version,
                        created_at=utcnow(),
                        document=encode(document),
                    )
                )

    def backups(self, project_id: str) -> list[dict[str, Any]]:
        query = (
            sa.select(project_backups)
            .where(project_backups.c.project_id == project_id)
            .order_by(project_backups.c.schema_version)
        )
        with self.engine.connect() as connection:
            return [
                {
                    "schema_version": row.schema_version,
                    "created_at": _iso(row.created_at),
                    "document": decode(row.document),
                }
                for row in connection.execute(query)
            ]

    # История отмены -----------------------------------------------------------

    def history(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        with self.engine.connect() as connection:
            return _load_history(connection, project_id)

    def reset_history(self, project_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                project_history.delete().where(project_history.c.project_id == project_id)
            )


class ProjectWrite:
    """Запись внутри транзакции: текущий документ, замена с проверкой ревизии
    и история — всё в одной транзакции."""

    def __init__(self, connection: Connection, project_id: str) -> None:
        self.connection = connection
        self.project_id = project_id

    def current(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            sa.select(projects.c.document)
            .where(projects.c.id == self.project_id)
            .where(projects.c.trashed_at.is_(None))
        ).first()
        return None if row is None else decode(row.document)

    def replace(self, document: dict[str, Any], expected_revision: int) -> None:
        result = self.connection.execute(
            projects.update()
            .where(projects.c.id == self.project_id)
            .where(projects.c.revision == expected_revision)
            .where(projects.c.trashed_at.is_(None))
            .values(
                document=encode(document),
                name=document["name"],
                original_filename=document.get("original_filename", ""),
                revision=int(document["configuration"]["revision"]),
                updated_at=utcnow(),
            )
        )
        if result.rowcount != 1:
            raise RevisionMismatchError(self.project_id)

    def update_history(
        self,
        change: Callable[[dict[str, list[dict[str, Any]]]], dict[str, list[dict[str, Any]]] | None],
    ) -> None:
        """Изменить стеки истории; `None` от функции — очистить историю."""
        stacks = _load_history(self.connection, self.project_id)
        updated = change(stacks)
        self.connection.execute(
            project_history.delete().where(project_history.c.project_id == self.project_id)
        )
        if updated is not None and (updated["undo"] or updated["redo"]):
            self.connection.execute(
                project_history.insert().values(
                    project_id=self.project_id,
                    stacks=json.dumps(updated, ensure_ascii=False),
                )
            )


def _load_history(connection: Connection, project_id: str) -> dict[str, list[dict[str, Any]]]:
    row = connection.execute(
        sa.select(project_history.c.stacks).where(project_history.c.project_id == project_id)
    ).first()
    if row is None:
        return {"undo": [], "redo": []}
    try:
        data = json.loads(row.stacks)
    except ValueError:
        # Испорченная история не мешает работать с проектом — она просто пуста.
        return {"undo": [], "redo": []}
    return {"undo": list(data.get("undo", [])), "redo": list(data.get("redo", []))}


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat() if value is not None else None
