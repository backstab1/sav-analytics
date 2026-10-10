"""Импорт файла в новый проект фоновым заданием (P5, GAP-014).

Запрос только принимает файл (размер и SHA-256 считаются по ходу записи) и
сразу отвечает заданием. Чтение метаданных, распознавание структуры и
запись проекта идут в очереди со стадиями, прогрессом и отменой; экран
опрашивает `GET /api/imports/{job_id}`. Прежний синхронный
`POST /api/projects` остаётся для скриптов и тестов.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from ..actor import current_actor_id
from ..api_dependencies import get_repository, get_settings
from ..import_jobs import KIND
from ..jobs import dispatch
from ..repository import InvalidUploadError, ProjectRepository
from ..settings import Settings

router = APIRouter(prefix="/api/imports", tags=["imports"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def start_import(
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    try:
        staged = repository.stage_upload(file.filename or "upload.sav", file.file)
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    queue = dispatch.queue_for(repository)
    job = queue.enqueue(
        KIND,
        {"name": name, "staged": staged},
        title=f"Импорт «{staged['original_filename']}»",
        max_attempts=2,
        timeout_seconds=settings.import_timeout_seconds,
        created_by=current_actor_id(),
    )
    dispatch.submit(repository, job)
    return _payload(job)


@router.get("/{job_id}")
def import_status(
    job_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict[str, Any]:
    return _payload(_job(repository, job_id))


@router.post("/{job_id}/cancel")
def cancel_import(
    job_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict[str, Any]:
    job = _job(repository, job_id)
    job = dispatch.queue_for(repository).cancel(job["id"])
    if job["status"] == "cancelled":
        repository.discard_staged(job["payload"]["staged"])
    return _payload(job)


def _job(repository: ProjectRepository, job_id: UUID) -> dict[str, Any]:
    job = dispatch.queue_for(repository).find(str(job_id))
    if job is None or job["kind"] != KIND:
        raise HTTPException(status_code=404, detail="Задание импорта не найдено.")
    return job


def _payload(job: dict[str, Any]) -> dict[str, Any]:
    result = job["result"] or {}
    return {
        "job_id": job["id"],
        "status": job["status"],
        "stage": job["stage"],
        "completed": job["completed"],
        "total": job["total"],
        "progress": round(job["completed"] / job["total"] * 100) if job["total"] else 0,
        "error": job["error"],
        "error_code": job["error_code"],
        "project_id": result.get("project_id"),
        "filename": job["payload"]["staged"]["original_filename"],
    }
