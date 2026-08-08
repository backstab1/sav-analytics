from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..api_dependencies import get_repository
from ..api_schemas import RecodeDefinition
from ..core.configuration_integrity import ConfigurationIntegrityError
from ..core.recoding import RecodingError, calculate_recode_preview, validate_recode
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(prefix="/api/projects/{project_id}/recodings", tags=["recodings"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_recoding(
    project_id: UUID,
    definition: RecodeDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_recode(payload, project["inspection"]["variables"])
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
        validate_recode(payload, project["inspection"]["variables"])
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
        validate_recode(recoding, project["inspection"]["variables"])
        return calculate_recode_preview(repository.source_path(project_id), recoding)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или перекодировка не найдены.") from exc
    except RecodingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
