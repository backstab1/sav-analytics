from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import FormulaDefinition
from ..core.configuration_integrity import ConfigurationIntegrityError
from ..core.formulas import FormulaError, formula_preview
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}/formulas", tags=["formulas"], route_class=ProjectRoute
)


@router.post("/preview")
def preview_formula(
    project_id: UUID,
    definition: FormulaDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    formula_id: UUID | None = None,
) -> dict:
    """Итог формулы до сохранения: база, пропуски по полям, среднее и размах."""
    try:
        project = repository.get(project_id)
        payload = definition.model_dump()
        if formula_id is not None:
            payload["id"] = str(formula_id)
        return formula_preview(repository.source_path(project_id), payload, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except FormulaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_formula(
    project_id: UUID,
    definition: FormulaDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.create_formula(project_id, definition.model_dump())
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{formula_id}")
def update_formula(
    project_id: UUID,
    formula_id: UUID,
    definition: FormulaDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.update_formula(project_id, formula_id, definition.model_dump())
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или формула не найдены.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{formula_id}")
def delete_formula(
    project_id: UUID,
    formula_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_formula(project_id, formula_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или формула не найдены.") from exc
    except ConfigurationIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
