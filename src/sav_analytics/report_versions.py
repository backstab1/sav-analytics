"""Неизменяемые версии собранных отчётов (P3).

Каждая сборка записывает, из чего и чем она получена: SHA-256 исходника,
ревизию и полную конфигурацию, версии схемы, кэша, приложения и
расчётного стека, автора и суммы файлов. По этой записи отчёт можно
воспроизвести и доказать, что скачанный файл — тот самый.
"""

from __future__ import annotations

import json
import platform
from functools import lru_cache
from importlib import metadata as package_metadata
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from . import __version__
from .actor import current_actor_id
from .db.engine import aware, utcnow
from .db.schema import report_versions
from .project_models import CONFIGURATION_SCHEMA_VERSION

_STACK = ("numpy", "pandas", "scipy", "pyreadstat", "XlsxWriter")


@lru_cache
def environment() -> dict[str, Any]:
    from .report_cache import REPORT_CACHE_VERSION

    versions: dict[str, str | None] = {}
    for name in _STACK:
        try:
            versions[name] = package_metadata.version(name)
        except package_metadata.PackageNotFoundError:
            versions[name] = None
    return {
        "app": __version__,
        "report_cache": REPORT_CACHE_VERSION,
        "configuration_schema": CONFIGURATION_SCHEMA_VERSION,
        "python": platform.python_version(),
        "packages": versions,
    }


def record(
    engine: sa.engine.Engine,
    *,
    project_id: str,
    artifact_id: str,
    kind: str,
    project: dict[str, Any],
    files: dict[str, Any],
    job_id: str | None = None,
) -> None:
    """Записать версию; повторная сборка того же артефакта версию не меняет."""
    configuration = project.get("configuration", {})
    try:
        with engine.begin() as connection:
            connection.execute(
                report_versions.insert().values(
                    project_id=project_id,
                    artifact_id=artifact_id,
                    kind=kind,
                    job_id=job_id,
                    created_at=utcnow(),
                    created_by=current_actor_id(),
                    configuration_revision=int(configuration.get("revision", 1)),
                    source_sha256=(project.get("source") or {}).get("sha256"),
                    schema_version=configuration.get("schema_version"),
                    environment=json.dumps(environment(), ensure_ascii=False),
                    parameters=json.dumps(configuration, ensure_ascii=False, sort_keys=True),
                    files=json.dumps(files, ensure_ascii=False),
                )
            )
    except IntegrityError:
        pass


def get(engine: sa.engine.Engine, project_id: str, artifact_id: str) -> dict[str, Any] | None:
    query = (
        sa.select(report_versions)
        .where(report_versions.c.project_id == project_id)
        .where(report_versions.c.artifact_id == artifact_id)
    )
    with engine.connect() as connection:
        row = connection.execute(query).first()
    return None if row is None else _as_dict(row)


def authors(engine: sa.engine.Engine, project_id: str) -> dict[str, str | None]:
    """Автор каждой сборки проекта: artifact_id → id пользователя."""
    query = sa.select(report_versions.c.artifact_id, report_versions.c.created_by).where(
        report_versions.c.project_id == project_id
    )
    with engine.connect() as connection:
        return {row.artifact_id: row.created_by for row in connection.execute(query)}


def delete_for_project(engine: sa.engine.Engine, project_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            report_versions.delete().where(report_versions.c.project_id == project_id)
        )


def _as_dict(row: Any) -> dict[str, Any]:
    data = dict(row._mapping)
    data.pop("id", None)
    created = aware(data["created_at"])
    data["created_at"] = created.isoformat() if created else None
    for key in ("environment", "parameters", "files"):
        data[key] = json.loads(data[key])
    return data
