from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import RangeSuggestionRequest, RecodeDefinition, SegmentSuggestionRequest
from ..core.configuration_integrity import ConfigurationIntegrityError
from ..core.recoding import (
    RecodingError,
    calculate_recode_preview,
    recode_source_values,
    suggest_ranges,
    validate_recode,
)
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}/recodings", tags=["recodings"], route_class=ProjectRoute
)


@router.get("/source-values")
def recoding_source_values(
    project_id: UUID,
    variable: Annotated[str, Query(min_length=1, max_length=64)],
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project = repository.get(project_id)
        source = next(
            (item for item in project["inspection"]["variables"] if item["name"] == variable),
            None,
        )
        if source is None:
            raise RecodingError("Исходная переменная не найдена в SAV.")
        return recode_source_values(repository.source_path(project_id), source, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except RecodingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/suggest-ranges")
def suggest_recoding_ranges(
    project_id: UUID,
    request: RangeSuggestionRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Диапазоны по квантилям или равным интервалам — заготовка для редактора."""
    try:
        project = repository.get(project_id)
        source = next(
            (
                item
                for item in project["inspection"]["variables"]
                if item["name"] == request.variable
            ),
            None,
        )
        if source is None:
            raise RecodingError("Исходная переменная не найдена в SAV.")
        return suggest_ranges(
            repository.source_path(project_id), source, request.method, request.groups, project
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except RecodingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/segments/suggest")
def suggest_segment_count(
    project_id: UUID,
    request: SegmentSuggestionRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Варианты числа сегментов с силуэтом и размерами — до сохранения."""
    from ..core.segmentation import SegmentationError, suggest_segments

    try:
        project = repository.get(project_id)
        return suggest_segments(
            repository.source_path(project_id),
            project,
            request.variables,
            request.k_min,
            max(request.k_min, request.k_max),
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except SegmentationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _fitted(payload: dict, project: dict, repository: ProjectRepository, project_id: UUID) -> dict:
    """Сегментации сервер сам считает центры: клиент их не присылает."""
    if payload.get("mode") != "segments":
        return payload
    from ..core.segmentation import SegmentationError, build_segment_definition

    try:
        return build_segment_definition(repository.source_path(project_id), project, payload)
    except SegmentationError as exc:
        raise RecodingError(str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_recoding(
    project_id: UUID,
    definition: RecodeDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_recode(payload, project["inspection"]["variables"], project)
        payload = _fitted(payload, project, repository, project_id)
        return repository.create_recoding(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except (RecodingError, InvalidUploadError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{recoding_id}")
def update_recoding(
    project_id: UUID,
    recoding_id: UUID,
    definition: RecodeDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_recode(payload, project["inspection"]["variables"], project)
        payload = _fitted(payload, project, repository, project_id)
        return repository.update_recoding(project_id, recoding_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или перекодировка не найдены.") from exc
    except (RecodingError, InvalidUploadError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{recoding_id}")
def delete_recoding(
    project_id: UUID,
    recoding_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_recoding(project_id, recoding_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или перекодировка не найдены.") from exc
    except ConfigurationIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{recoding_id}/preview")
def preview_recoding(
    project_id: UUID,
    recoding_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project, recoding = repository.recoding(project_id, recoding_id)
        validate_recode(recoding, project["inspection"]["variables"], project)
        return calculate_recode_preview(repository.source_path(project_id), recoding, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или перекодировка не найдены.") from exc
    except RecodingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
