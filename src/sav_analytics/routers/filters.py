from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import FilterDefinition, QuestionBaseUpdate
from ..core.configuration_integrity import ConfigurationIntegrityError
from ..core.filtering import (
    FilterError,
    calculate_filter_preview,
    condition_source_options,
    validate_filter,
)
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}", tags=["filters"], route_class=ProjectRoute
)


@router.get("/filters/source-options")
def filter_source_options(
    project_id: UUID,
    kind: Annotated[Literal["question", "recoding"], Query()],
    ref: Annotated[str, Query(min_length=1, max_length=64)],
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project = repository.get(project_id)
        return condition_source_options(
            repository.source_path(project_id), {"kind": kind, "ref": ref}, project
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/filters", status_code=status.HTTP_201_CREATED)
def create_filter(
    project_id: UUID,
    definition: FilterDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_filter(payload, project)
        return repository.create_filter(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/filters/{filter_id}")
def update_filter(
    project_id: UUID,
    filter_id: UUID,
    definition: FilterDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_filter(payload, project)
        return repository.update_filter(project_id, filter_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или фильтр не найдены.") from exc
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/filters/{filter_id}")
def delete_filter(
    project_id: UUID,
    filter_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_filter(project_id, filter_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или фильтр не найдены.") from exc
    except (InvalidUploadError, ConfigurationIntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/filters/{filter_id}/preview")
def preview_filter(
    project_id: UUID,
    filter_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project, definition = repository.filter(project_id, filter_id)
        return calculate_filter_preview(repository.source_path(project_id), definition, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или фильтр не найдены.") from exc
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/filters/preview")
def preview_filter_draft(
    project_id: UUID,
    definition: FilterDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        return calculate_filter_preview(repository.source_path(project_id), payload, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/report-filter")
def assign_report_filter(
    project_id: UUID,
    update: QuestionBaseUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.assign_report_filter(project_id, update.filter_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или фильтр не найдены.") from exc
