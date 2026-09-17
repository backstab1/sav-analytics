from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import AnalysisCardCreate
from ..core.association import AssociationError, analyse_cards, variable_profile
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}/analysis", tags=["analysis"], route_class=ProjectRoute
)


@router.get("/cards")
def list_cards(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Все карточки рабочей области, посчитанные вместе: поправка BH — на все."""
    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    cards = project["configuration"].get("analysis_cards", [])
    return {"cards": analyse_cards(source, project, cards)}


@router.get("/variable")
def describe_variable(
    project_id: UUID,
    kind: Literal["question", "recoding"],
    ref: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Карточка переменной: распределение или среднее с разбросом и пропуски."""
    try:
        project = repository.get(project_id)
        return variable_profile(
            repository.source_path(project_id), project, {"kind": kind, "ref": ref}
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except AssociationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cards", status_code=status.HTTP_201_CREATED)
def add_card(
    project_id: UUID,
    card: AnalysisCardCreate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.add_analysis_card(
            project_id, card.a.model_dump(), card.b.model_dump()
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или переменная не найдены.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/cards/{card_id}")
def delete_card(
    project_id: UUID,
    card_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_analysis_card(project_id, card_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или карточка не найдены.") from exc
