from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import (
    AnalysisCardCreate,
    AnalysisModelCreate,
    GaborGrangerRequest,
    TurfRequest,
    VanWestendorpRequest,
)
from ..core.association import AssociationError, analyse_cards, variable_profile
from ..core.regression import fit_models
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


@router.get("/models")
def list_models(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Сохранённые модели, посчитанные на общем фильтре и весе отчёта."""
    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    models = project["configuration"].get("analysis_models", [])
    return {"models": fit_models(source, project, models)}


@router.post("/models", status_code=status.HTTP_201_CREATED)
def add_model(
    project_id: UUID,
    model: AnalysisModelCreate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.add_analysis_model(project_id, model.model_dump())
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или переменная не найдены.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/models/{model_id}")
def delete_model(
    project_id: UUID,
    model_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_analysis_model(project_id, model_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или модель не найдены.") from exc


def _method(project_id: UUID, repository: ProjectRepository, run) -> dict:  # type: ignore[no-untyped-def]
    """Методы считаются по запросу и в проект не сохраняются."""
    from ..core.research_methods import MethodError

    try:
        project = repository.get(project_id)
        return run(repository.source_path(project_id), project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except MethodError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/methods/turf")
def run_turf(
    project_id: UUID,
    request: TurfRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """TURF: портфели вариантов с наибольшим охватом (PQ.15)."""
    from ..core.research_methods import turf

    def run(path, project):  # type: ignore[no-untyped-def]
        return turf(path, project, request.code, request.max_size)

    return _method(project_id, repository, run)


@router.post("/methods/van-westendorp")
def run_van_westendorp(
    project_id: UUID,
    request: VanWestendorpRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Van Westendorp: оптимальная цена и приемлемый диапазон (PQ.15)."""
    from ..core.research_methods import van_westendorp

    def run(path, project):  # type: ignore[no-untyped-def]
        return van_westendorp(path, project, request.model_dump())

    return _method(project_id, repository, run)


@router.post("/methods/gabor-granger")
def run_gabor_granger(
    project_id: UUID,
    request: GaborGrangerRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Gabor–Granger: кривая спроса и цена наибольшей выручки (PQ.15)."""
    from ..core.research_methods import gabor_granger

    steps = [step.model_dump() for step in request.steps]
    return _method(
        project_id, repository, lambda path, project: gabor_granger(path, project, steps)
    )
