from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from .report_cache import get_cached_report, prepare_report, report_cache_key
from .repository import ProjectRepository

JobStatus = Literal["queued", "running", "complete", "failed"]
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report-builder")
_guard = threading.Lock()
_jobs: dict[str, ReportJob] = {}
_active_by_key: dict[str, str] = {}


@dataclass
class ReportJob:
    id: str
    project_id: str
    cache_key: str
    status: JobStatus
    completed: int = 0
    total: int = 1
    stage: str = "В очереди"
    error: str | None = None
    cached: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "project_id": self.project_id,
            "status": self.status,
            "completed": self.completed,
            "total": self.total,
            "progress": round(self.completed / self.total * 100) if self.total else 0,
            "stage": self.stage,
            "error": self.error,
            "cached": self.cached,
        }


def start_report_job(
    repository: ProjectRepository,
    project_id: UUID,
    project: dict[str, Any],
) -> dict[str, Any]:
    key = report_cache_key(project)
    active_key = f"{repository.root.resolve()}::{project_id}::{key}"
    cached = get_cached_report(repository, project_id, project)
    if cached is not None:
        job = ReportJob(
            id=str(uuid4()),
            project_id=str(project_id),
            cache_key=key,
            status="complete",
            completed=1,
            total=1,
            stage="Готово",
            cached=True,
        )
        with _guard:
            _jobs[job.id] = job
        return job.payload()

    with _guard:
        active_id = _active_by_key.get(active_key)
        if active_id is not None:
            return _jobs[active_id].payload()
        job = ReportJob(
            id=str(uuid4()),
            project_id=str(project_id),
            cache_key=key,
            status="queued",
        )
        _jobs[job.id] = job
        _active_by_key[active_key] = job.id
    _executor.submit(_run_report_job, repository, project_id, project, job.id, active_key)
    return job.payload()


def get_report_job(job_id: UUID, project_id: UUID) -> dict[str, Any] | None:
    with _guard:
        job = _jobs.get(str(job_id))
        if job is None or job.project_id != str(project_id):
            return None
        return job.payload()


def _run_report_job(
    repository: ProjectRepository,
    project_id: UUID,
    project: dict[str, Any],
    job_id: str,
    active_key: str,
) -> None:
    def progress(completed: int, total: int, stage: str) -> None:
        with _guard:
            job = _jobs[job_id]
            job.status = "running"
            job.completed = completed
            job.total = max(1, total)
            job.stage = stage

    try:
        with _guard:
            _jobs[job_id].status = "running"
            _jobs[job_id].stage = "Подготовка"
        prepared = prepare_report(
            repository,
            project_id,
            project,
            progress_callback=progress,
        )
        with _guard:
            job = _jobs[job_id]
            job.status = "complete"
            job.completed = job.total
            job.stage = "Готово"
            job.cached = prepared.cached
    except Exception as exc:
        with _guard:
            job = _jobs[job_id]
            job.status = "failed"
            job.stage = "Ошибка"
            job.error = str(exc) or type(exc).__name__
    finally:
        with _guard:
            _active_by_key.pop(active_key, None)
