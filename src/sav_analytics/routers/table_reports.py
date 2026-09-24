from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import TableReportCreate, TableReportLayout, TableReportRename
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}/tables/reports",
    tags=["tables"],
    route_class=ProjectRoute,
)

_NOT_FOUND = "Проект или таблица не найдены."


@router.post("", status_code=status.HTTP_201_CREATED)
def create_table_report(
    project_id: UUID,
    request: TableReportCreate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    layout = request.model_dump(mode="json", exclude={"name"})
    try:
        return repository.create_table_report(project_id, request.name, layout)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{report_id}")
def update_table_report(
    project_id: UUID,
    report_id: UUID,
    request: TableReportLayout,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Сохранить раскладку и вид таблицы; «Очистить» — та же запись пустой раскладки."""
    try:
        return repository.update_table_report(
            project_id, report_id, request.model_dump(mode="json")
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{report_id}")
def rename_table_report(
    project_id: UUID,
    report_id: UUID,
    request: TableReportRename,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.rename_table_report(project_id, report_id, request.name.strip())
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc


@router.post("/{report_id}/copy", status_code=status.HTTP_201_CREATED)
def copy_table_report(
    project_id: UUID,
    report_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.copy_table_report(project_id, report_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc


@router.delete("/{report_id}")
def delete_table_report(
    project_id: UUID,
    report_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_table_report(project_id, report_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
