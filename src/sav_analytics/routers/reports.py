from __future__ import annotations

import io
import re
import zipfile
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse

from .. import report_versions
from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..core import report_books
from ..core.preflight import PreflightBlockedError, run_preflight
from ..jobs import dispatch
from ..report_cache import (
    PreparedReport,
    ReportArtifactNotFoundError,
    get_cached_report,
    get_report_artifact,
    list_report_runs,
)
from ..report_jobs import get_report_job, job_payload, start_report_job
from ..repository import ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}/reports", tags=["reports"], route_class=ProjectRoute
)


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


@router.get("/history")
def report_history(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project = repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    return {"runs": list_report_runs(repository, project_id, project)}


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


@router.post("/prepare-all")
def prepare_all_report_books(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Собрать все книги проекта одним действием, не меняя выбранную.

    Каждая книга — своё обычное задание на копии проекта, где она выбрана, и
    свой артефакт; задания идут друг за другом в той же очереди сборки. Книга,
    которую не пропускает проверка, не собирается и возвращается со своими
    ошибками — остальные от неё не зависят.
    """
    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    result = []
    for book in report_books.books(project["configuration"]):
        book_project = report_books.project_for_book(project, book["id"])
        entry: dict = {"book_id": book["id"], "book": book["name"], "active": book["active"]}
        if get_cached_report(repository, project_id, book_project) is None:
            preflight = run_preflight(source, book_project)
            if not preflight.can_prepare:
                result.append({**entry, "status": "blocked", "preflight": preflight.to_dict()})
                continue
        result.append({**entry, **start_report_job(repository, project_id, book_project)})
    return {"books": result}


@router.get("/bundle.zip")
def download_report_bundle(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    artifact: Annotated[list[str], Query()],
) -> Response:
    """Готовые сборки одним архивом: Excel и statistics.txt каждой книги."""
    try:
        project = repository.get(project_id)
        prepared = [get_report_artifact(repository, project_id, item) for item in artifact]
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except ReportArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Артефакт отчёта не найден.") from exc
    buffer = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, item in enumerate(prepared, start=1):
            stem = _safe_name(item.book or f"Отчёт {index}")
            # Названия книг не обязаны быть разными.
            if stem in used:
                stem = f"{stem} ({index})"
            used.add(stem)
            archive.write(item.topline_path, f"{stem}_topline.xlsx")
            archive.write(item.statistics_path, f"{stem}_statistics.txt")
            if item.presentation_path is not None:
                archive.write(item.presentation_path, f"{stem}.pptx")
    filename = f"{_safe_name(str(project['name']))}_книги.zip"
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/jobs/{job_id}")
def report_job_status(
    project_id: UUID,
    job_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    job = get_report_job(repository, job_id, project_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача формирования отчёта не найдена.")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_report_job(
    project_id: UUID,
    job_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Отменить сборку: из очереди — сразу, идущую — на ближайшем шаге."""
    if get_report_job(repository, job_id, project_id) is None:
        raise HTTPException(status_code=404, detail="Задача формирования отчёта не найдена.")
    dispatch.queue_for(repository).cancel(str(job_id))
    return get_report_job(repository, job_id, project_id) or {}


@router.post("/jobs/{job_id}/retry")
def retry_report_job(
    project_id: UUID,
    job_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Повторить провалившуюся или отменённую сборку с тем же снимком."""
    if get_report_job(repository, job_id, project_id) is None:
        raise HTTPException(status_code=404, detail="Задача формирования отчёта не найдена.")
    job = dispatch.queue_for(repository).retry(str(job_id))
    dispatch.submit(repository, job)
    return job_payload(repository, job)


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


@router.get("/presentation.pptx")
def download_current_presentation(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> FileResponse:
    project, prepared = _current_prepared_report(repository, project_id)
    return _presentation_response(project, prepared)


@router.get("/artifacts/{artifact_id}/presentation.pptx")
def download_artifact_presentation(
    project_id: UUID,
    artifact_id: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> FileResponse:
    project, prepared = _prepared_artifact(repository, project_id, artifact_id)
    return _presentation_response(project, prepared)


@router.get("/artifacts/{artifact_id}/version")
def report_artifact_version(
    project_id: UUID,
    artifact_id: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Из чего и чем собрана сборка: исходник, конфигурация, версии, суммы."""
    _prepared_artifact(repository, project_id, artifact_id)
    version = report_versions.get(repository.engine, str(project_id), artifact_id)
    if version is None:
        raise HTTPException(
            status_code=404, detail="Сборка сделана до учёта версий: записи о ней нет."
        )
    return version


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


_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _safe_name(name: str) -> str:
    return _UNSAFE_FILENAME.sub("_", name).strip(" .") or "Отчёт"


def _file_stem(project: dict, prepared: PreparedReport) -> str:
    """Имя файла книги: при нескольких книгах к проекту добавляется собранная.

    Название берётся из манифеста сборки, а не из выбранной сейчас книги:
    из истории и после «Собрать все книги» скачивают и не выбранные.
    """
    configuration = project["configuration"]
    if len(configuration.get("reports") or []) < 2:
        return str(project["name"])
    book = prepared.book or report_books.active_book_name(configuration)
    return f"{project['name']}_{book}" if book else str(project["name"])


def _topline_response(project: dict, prepared: PreparedReport) -> FileResponse:
    return FileResponse(
        prepared.topline_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{_file_stem(project, prepared)}_topline.xlsx",
    )


def _presentation_response(project: dict, prepared: PreparedReport) -> FileResponse:
    if prepared.presentation_path is None:
        raise HTTPException(
            status_code=404,
            detail="В этой сборке презентации нет: она включается в книге галочкой «PPTX».",
        )
    return FileResponse(
        prepared.presentation_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{_file_stem(project, prepared)}.pptx",
    )


def _statistics_response(project: dict, prepared: PreparedReport) -> FileResponse:
    return FileResponse(
        prepared.statistics_path,
        media_type="text/plain; charset=utf-8",
        filename=f"{_file_stem(project, prepared)}_statistics.txt",
    )
