from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from .core.report import build_topline_artifacts
from .repository import ProjectRepository

REPORT_CACHE_VERSION = 4
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


@dataclass(frozen=True)
class PreparedReport:
    topline_path: Path
    statistics_path: Path
    cached: bool


def prepare_report(
    repository: ProjectRepository,
    project_id: UUID,
    project: dict[str, Any],
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> PreparedReport:
    cache_key = _cache_key(project)
    lock_key = f"{repository.root.resolve()}::{project_id}"
    with _locks_guard:
        lock = _locks.setdefault(lock_key, threading.Lock())

    with lock:
        cached = _cached_report(repository, project_id, cache_key)
        if cached is not None:
            return cached

        cache_dir = repository.report_cache_dir(project_id)
        cache_dir.mkdir(exist_ok=True)
        topline_path = cache_dir / "topline.xlsx"
        statistics_path = cache_dir / "statistics.txt"
        manifest_path = cache_dir / "manifest.json"
        topline_temporary = cache_dir / ".topline.xlsx.tmp"
        statistics_temporary = cache_dir / ".statistics.txt.tmp"
        manifest_temporary = cache_dir / ".manifest.json.tmp"

        try:
            with statistics_temporary.open("w", encoding="utf-8", newline="\n") as stream:
                artifacts = build_topline_artifacts(
                    repository.source_path(project_id),
                    project,
                    statistics_stream=stream,
                    progress_callback=progress_callback,
                )
            topline_temporary.write_bytes(artifacts.xlsx)
            manifest_temporary.write_text(
                json.dumps({"cache_key": cache_key}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(topline_temporary, topline_path)
            os.replace(statistics_temporary, statistics_path)
            os.replace(manifest_temporary, manifest_path)
        finally:
            topline_temporary.unlink(missing_ok=True)
            statistics_temporary.unlink(missing_ok=True)
            manifest_temporary.unlink(missing_ok=True)

        return PreparedReport(topline_path, statistics_path, cached=False)


def get_cached_report(
    repository: ProjectRepository,
    project_id: UUID,
    project: dict[str, Any],
) -> PreparedReport | None:
    return _cached_report(repository, project_id, report_cache_key(project))


def report_cache_key(project: dict[str, Any]) -> str:
    return _cache_key(project)


def _cached_report(
    repository: ProjectRepository,
    project_id: UUID,
    cache_key: str,
) -> PreparedReport | None:
    cache_dir = repository.report_cache_dir(project_id)
    topline_path = cache_dir / "topline.xlsx"
    statistics_path = cache_dir / "statistics.txt"
    manifest_path = cache_dir / "manifest.json"
    if not topline_path.is_file() or not statistics_path.is_file() or not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if manifest.get("cache_key") != cache_key:
        return None
    return PreparedReport(topline_path, statistics_path, cached=True)


def _cache_key(project: dict[str, Any]) -> str:
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
    return hashlib.sha256(encoded).hexdigest()
