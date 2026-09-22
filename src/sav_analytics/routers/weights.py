from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import CalculatedWeightDefinition, WeightTargetTemplateRequest
from ..core.configuration_integrity import ConfigurationIntegrityError
from ..core.weight_targets import WeightTargetError, build_target_template, read_target_file
from ..core.weight_validation import assess_project_weight
from ..core.weighting import WeightingError, build_raking_export, calculate_raking_preview
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}/weights", tags=["weights"], route_class=ProjectRoute
)


@router.get("/ready/{variable}/diagnostics")
def ready_weight_diagnostics(
    project_id: UUID,
    variable: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Разбор готового веса до его применения.

    Отдаёт и вердикт, и числа: `requirements.md` §8 требует показывать
    распределение веса перед применением, а не только сообщать об отказе.
    """

    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    return assess_project_weight(source, variable, project).to_dict()


@router.post("/targets-template")
def download_target_template(
    project_id: UUID,
    request: WeightTargetTemplateRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> StreamingResponse:
    """Шаблон целей по переменным редактора — заполнить в Excel и загрузить обратно."""
    try:
        repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    content = build_target_template(request.model_dump(mode="json"))
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''weight_targets.xlsx"},
    )


@router.post("/targets-import")
def import_targets(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    file: Annotated[UploadFile, File()],
) -> dict:
    """Строки заполненного шаблона. Расставляет их по редактору интерфейс."""
    try:
        repository.get(project_id)
        return {"rows": read_target_file(file.filename or "", file.file)}
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except WeightTargetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_calculated_weight(
    project_id: UUID,
    definition: CalculatedWeightDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        calculate_raking_preview(repository.source_path(project_id), payload, project)
        return repository.create_calculated_weight(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except (WeightingError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{weight_id}")
def update_calculated_weight(
    project_id: UUID,
    weight_id: UUID,
    definition: CalculatedWeightDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        calculate_raking_preview(repository.source_path(project_id), payload, project)
        return repository.update_calculated_weight(project_id, weight_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вес не найдены.") from exc
    except (WeightingError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{weight_id}")
def delete_calculated_weight(
    project_id: UUID,
    weight_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_calculated_weight(project_id, weight_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вес не найдены.") from exc
    except (InvalidUploadError, ConfigurationIntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{weight_id}/preview")
def preview_calculated_weight(
    project_id: UUID,
    weight_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project, definition = repository.calculated_weight(project_id, weight_id)
        return calculate_raking_preview(repository.source_path(project_id), definition, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вес не найдены.") from exc
    except (WeightingError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{weight_id}/export.xlsx")
def download_calculated_weight(
    project_id: UUID,
    weight_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> StreamingResponse:
    try:
        project, definition = repository.calculated_weight(project_id, weight_id)
        content = build_raking_export(repository.source_path(project_id), definition, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вес не найдены.") from exc
    except (WeightingError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = f"{project['name']}_{definition['name']}_weight.xlsx"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )
