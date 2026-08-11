from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..api_dependencies import get_repository
from ..core.preflight import PreflightBlockedError, run_preflight
from ..report_cache import (
    PreparedReport,
    ReportArtifactNotFoundError,
    get_cached_report,
    get_report_artifact,
)
from ..report_jobs import get_report_job, start_report_job
from ..repository import ProjectNotFoundError, ProjectRepository

router = APIRouter(prefix="/api/projects/{project_id}/reports", tags=["reports"])


@router.get("/preflight")
def report_preflight(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    return run_preflight(source, project).to_dict()


@router.post("/prepare")
def prepare_project_report(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project = repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    # Готовый артефакт этой ревизии уже прошёл проверку при сборке, а повторный
    # preflight стоил бы лишнего чтения SAV на каждом скачивании.
    if get_cached_report(repository, project_id, project) is None:
        report = run_preflight(repository.source_path(project_id), project)
        if not report.can_prepare:
            raise PreflightBlockedError(report)
    return start_report_job(repository, project_id, project)


@router.get("/jobs/{job_id}")
def report_job_status(project_id: UUID, job_id: UUID) -> dict:
    job = get_report_job(job_id, project_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача формирования отчёта не найдена.")
    return job


@router.get("/topline.xlsx")
def download_current_topline(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> FileResponse:
    project, prepared = _current_prepared_report(repository, project_id)
    return _topline_response(project, prepared)


@router.get("/statistics.txt")
def download_current_statistics(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> FileResponse:
    project, prepared = _current_prepared_report(repository, project_id)
    return _statistics_response(project, prepared)


@router.get("/artifacts/{artifact_id}/topline.xlsx")
def download_artifact_topline(
    project_id: UUID,
    artifact_id: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> FileResponse:
    project, prepared = _prepared_artifact(repository, project_id, artifact_id)
    return _topline_response(project, prepared)


@router.get("/artifacts/{artifact_id}/statistics.txt")
def download_artifact_statistics(
    project_id: UUID,
    artifact_id: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> FileResponse:
    project, prepared = _prepared_artifact(repository, project_id, artifact_id)
    return _statistics_response(project, prepared)


def _current_prepared_report(
    repository: ProjectRepository,
    project_id: UUID,
) -> tuple[dict, PreparedReport]:
    try:
        project = repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    prepared = get_cached_report(repository, project_id, project)
    if prepared is None:
        raise HTTPException(
            status_code=409,
            detail="Для текущей версии настроек отчёт ещё не подготовлен.",
        )
    return project, prepared


def _prepared_artifact(
    repository: ProjectRepository,
    project_id: UUID,
    artifact_id: str,
) -> tuple[dict, PreparedReport]:
    try:
        project = repository.get(project_id)
        prepared = get_report_artifact(repository, project_id, artifact_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except ReportArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Артефакт отчёта не найден.") from exc
    return project, prepared


def _topline_response(project: dict, prepared: PreparedReport) -> FileResponse:
    return FileResponse(
        prepared.topline_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{project['name']}_topline.xlsx",
    )


def _statistics_response(project: dict, prepared: PreparedReport) -> FileResponse:
    return FileResponse(
        prepared.statistics_path,
        media_type="text/plain; charset=utf-8",
        filename=f"{project['name']}_statistics.txt",
    )
