from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from .core.report import build_topline_artifacts
from .repository import ProjectRepository

REPORT_CACHE_VERSION = 7

# Идентификатор артефакта становится именем каталога внутри
# projects/<uuid>/reports/artifacts/, поэтому его длина попадает в путь. Полные
# 64 знака sha256 доводили путь до предела Windows в 260 символов при вложенном
# SAV_ANALYTICS_DATA_DIR, и создание каталога падало с WinError 3. Ключи
# различаются в пределах одного проекта, где их десятки, а не миллиарды: 64 бит
# хватает с колоссальным запасом.
_ARTIFACT_ID_LENGTH = 16
_ARTIFACT_ID = re.compile(rf"^[0-9a-f]{{{_ARTIFACT_ID_LENGTH}}}$")
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


class ReportArtifactNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class PreparedReport:
    topline_path: Path
    statistics_path: Path
    cached: bool
    cache_key: str
    artifact_id: str
    configuration_revision: int


def prepare_report(
    repository: ProjectRepository,
    project_id: UUID,
    project: dict[str, Any],
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> PreparedReport:
    cache_key = report_cache_key(project)
    lock_key = f"{repository.root.resolve()}::{project_id}::{cache_key}"
    with _locks_guard:
        lock = _locks.setdefault(lock_key, threading.Lock())

    with lock:
        cached = _cached_report(repository, project_id, cache_key)
        if cached is not None:
            return cached

        artifact_dir = _artifact_dir(repository, project_id, cache_key)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        topline_path = artifact_dir / "topline.xlsx"
        statistics_path = artifact_dir / "statistics.txt"
        manifest_path = artifact_dir / "manifest.json"
        topline_temporary = artifact_dir / ".topline.xlsx.tmp"
        statistics_temporary = artifact_dir / ".statistics.txt.tmp"
        manifest_temporary = artifact_dir / ".manifest.json.tmp"
        revision = _configuration_revision(project)

        try:
            with statistics_temporary.open("w", encoding="utf-8", newline="\n") as stream:
                artifacts = build_topline_artifacts(
                    repository.source_path(project_id),
                    project,
                    statistics_stream=stream,
                    progress_callback=progress_callback,
                )
            topline_temporary.write_bytes(artifacts.xlsx)
            manifest = {
                "artifact_id": cache_key,
                "cache_key": cache_key,
                "cache_version": REPORT_CACHE_VERSION,
                "configuration_revision": revision,
                "source_sha256": project.get("source", {}).get("sha256"),
                "files": {
                    "topline.xlsx": _file_metadata(topline_temporary),
                    "statistics.txt": _file_metadata(statistics_temporary),
                },
            }
            manifest_temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(topline_temporary, topline_path)
            os.replace(statistics_temporary, statistics_path)
            # The manifest is the commit marker and is always installed last.
            os.replace(manifest_temporary, manifest_path)
        finally:
            topline_temporary.unlink(missing_ok=True)
            statistics_temporary.unlink(missing_ok=True)
            manifest_temporary.unlink(missing_ok=True)

        return PreparedReport(
            topline_path=topline_path,
            statistics_path=statistics_path,
            cached=False,
            cache_key=cache_key,
            artifact_id=cache_key,
            configuration_revision=revision,
        )


def get_cached_report(
    repository: ProjectRepository,
    project_id: UUID,
    project: dict[str, Any],
) -> PreparedReport | None:
    return _cached_report(repository, project_id, report_cache_key(project))


def get_report_artifact(
    repository: ProjectRepository,
    project_id: UUID,
    artifact_id: str,
) -> PreparedReport:
    if not _ARTIFACT_ID.fullmatch(artifact_id):
        raise ReportArtifactNotFoundError(artifact_id)
    prepared = _cached_report(repository, project_id, artifact_id)
    if prepared is None:
        raise ReportArtifactNotFoundError(artifact_id)
    return prepared


def report_cache_key(project: dict[str, Any]) -> str:
    payload = {
        "version": REPORT_CACHE_VERSION,
        "source_sha256": project.get("source", {}).get("sha256"),
        "configuration": project["configuration"],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:_ARTIFACT_ID_LENGTH]


def _cached_report(
    repository: ProjectRepository,
    project_id: UUID,
    cache_key: str,
) -> PreparedReport | None:
    artifact_dir = _artifact_dir(repository, project_id, cache_key)
    topline_path = artifact_dir / "topline.xlsx"
    statistics_path = artifact_dir / "statistics.txt"
    manifest_path = artifact_dir / "manifest.json"
    if not topline_path.is_file() or not statistics_path.is_file() or not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        revision = int(manifest["configuration_revision"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if manifest.get("cache_key") != cache_key or manifest.get("artifact_id") != cache_key:
        return None
    if not _files_match_manifest(artifact_dir, manifest):
        return None
    return PreparedReport(
        topline_path=topline_path,
        statistics_path=statistics_path,
        cached=True,
        cache_key=cache_key,
        artifact_id=cache_key,
        configuration_revision=revision,
    )


def _files_match_manifest(artifact_dir: Path, manifest: dict[str, Any]) -> bool:
    """Сверить файлы артефакта с тем, что записал собравший его job.

    Манифест всегда хранил размер и sha256 обоих файлов, но никто их не читал:
    достаточно было существования файлов, и обрезанный или подменённый XLSX
    выдавался как готовый отчёт — в том числе на скачивание. Размера мало,
    подмена той же длины ловится только суммой.
    """
    recorded = manifest.get("files")
    if not isinstance(recorded, dict) or not recorded:
        return False
    for name, expected in recorded.items():
        if not isinstance(expected, dict):
            return False
        path = artifact_dir / name
        try:
            content = path.read_bytes()
        except OSError:
            return False
        if len(content) != expected.get("size"):
            return False
        if hashlib.sha256(content).hexdigest() != expected.get("sha256"):
            return False
    return True


def _artifact_dir(
    repository: ProjectRepository,
    project_id: UUID,
    artifact_id: str,
) -> Path:
    return repository.report_cache_dir(project_id) / "artifacts" / artifact_id


def _configuration_revision(project: dict[str, Any]) -> int:
    return int(project.get("configuration", {}).get("revision", 1))


def _file_metadata(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
