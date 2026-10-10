"""Сборка отчёта — задание очереди (P5).

Задание держит снимок проекта: поздние правки не меняют его вход. Ключ
идемпотентности — ключ кэша, то есть версия конфигурации и исходника:
два одинаковых prepare не ставят два расчёта, второй получает первое
задание. Готовая сборка этой версии отдаётся сразу, без очереди.
Скачивание идёт только по `artifact_id` конкретного задания — файлы
неизменны и сверяются с манифестом (`report_cache.py`).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from .actor import current_actor_id
from .jobs import dispatch
from .jobs.runtime import JobContext, JobFailure, register
from .report_cache import (
    ReportArtifactNotFoundError,
    get_cached_report,
    get_report_artifact,
    prepare_report,
    report_cache_key,
    report_downloads,
)
from .repository import ProjectNotFoundError, ProjectRepository

KIND = "report.build"
FAILURE = "Не удалось сформировать отчёт. Проверьте настройки и повторите попытку."


def start_report_job(
    repository: ProjectRepository,
    project_id: UUID,
    project: dict[str, Any],
) -> dict[str, Any]:
    snapshot = deepcopy(project)
    key = report_cache_key(snapshot)
    revision = int(snapshot.get("configuration", {}).get("revision", 1))
    queue = dispatch.queue_for(repository)
    meta = {"cache_key": key, "configuration_revision": revision}
    cached = get_cached_report(repository, project_id, snapshot)
    if cached is not None:
        job = queue.enqueue(
            KIND, {**meta, "cached": True},
            project_id=str(project_id), title="Сборка отчёта", created_by=current_actor_id(),
        )
        job = queue.record_complete(
            job["id"], {"artifact_id": cached.artifact_id, "cached": True}
        )
        return job_payload(repository, job)
    settings = dispatch._settings()
    job = queue.enqueue(
        KIND,
        {**meta, "project": snapshot},
        project_id=str(project_id),
        title="Сборка отчёта",
        idempotency_key=f"report:{project_id}:{key}",
        max_attempts=2,
        timeout_seconds=settings.report_timeout_seconds,
        created_by=current_actor_id(),
    )
    dispatch.submit(repository, job)
    return job_payload(repository, job)


def get_report_job(
    repository: ProjectRepository, job_id: UUID, project_id: UUID
) -> dict[str, Any] | None:
    job = dispatch.queue_for(repository).find(str(job_id))
    if job is None or job["project_id"] != str(project_id) or job["kind"] != KIND:
        return None
    return job_payload(repository, job)


@register(KIND, failure_message=FAILURE, failure_code="REPORT_BUILD_FAILED")
def _build(context: JobContext) -> dict[str, Any]:
    project_id = UUID(context.project_id or "")
    context.progress(0, 1, "Подготовка")
    try:
        prepared = prepare_report(
            context.repository,
            project_id,
            context.payload["project"],
            progress_callback=context.progress,
        )
    except ProjectNotFoundError as exc:
        raise JobFailure("Проект удалён.", "REPORT_BUILD_FAILED") from exc
    return {"artifact_id": prepared.artifact_id, "cached": prepared.cached}


def job_payload(repository: ProjectRepository, job: dict[str, Any]) -> dict[str, Any]:
    """Задание в прежнем виде ответа API — экран и тесты от него зависят."""
    result = job["result"] or {}
    artifact_id = result.get("artifact_id")
    downloads = None
    if artifact_id is not None:
        base = f"/api/projects/{job['project_id']}/reports/artifacts/{artifact_id}"
        try:
            prepared = get_report_artifact(repository, UUID(job["project_id"]), artifact_id)
        except (ReportArtifactNotFoundError, ProjectNotFoundError, ValueError):
            prepared = None
        downloads = report_downloads(base, prepared)
    payload = job["payload"]
    return {
        "job_id": job["id"],
        "project_id": job["project_id"],
        "configuration_revision": payload.get("configuration_revision", 1),
        "cache_key": payload.get("cache_key"),
        "artifact_id": artifact_id,
        "downloads": downloads,
        "status": job["status"],
        "completed": job["completed"],
        "total": job["total"],
        "progress": round(job["completed"] / job["total"] * 100) if job["total"] else 0,
        "stage": job["stage"],
        "error": job["error"],
        "error_code": job["error_code"],
        "cached": bool(result.get("cached", False)),
        "attempts": job["attempts"],
        "cancel_requested": job["cancel_requested"],
    }
