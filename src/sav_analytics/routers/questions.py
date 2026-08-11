from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..api_dependencies import get_repository
from ..api_schemas import (
    NotApplicableUpdate,
    QuestionBaseUpdate,
    QuestionOrder,
    QuestionUpdate,
)
from ..core.not_applicable import suggest_not_applicable_codes
from ..core.topline import ToplineError, calculate_preview
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(prefix="/api/projects/{project_id}/questions", tags=["questions"])


@router.get("/not-applicable-suggestions")
def not_applicable_suggestions(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    groups = suggest_not_applicable_codes(source, project)
    return {"groups": [group.to_dict() for group in groups]}


@router.post("/not-applicable")
def mark_not_applicable(
    project_id: UUID,
    update: NotApplicableUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    marks = [mark.model_dump(mode="json") for mark in update.marks]
    try:
        return repository.mark_not_applicable(project_id, marks)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вопрос не найден.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{code}")
def update_question(
    project_id: UUID,
    code: str,
    update: QuestionUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    changes = update.model_dump(exclude_none=True, mode="json")
    try:
        return repository.update_question(project_id, code, changes)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вопрос не найден.") from exc
    except (ToplineError, InvalidUploadError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/order")
def reorder_questions(
    project_id: UUID,
    order: QuestionOrder,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.reorder_questions(project_id, order.codes)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{code}/preview")
def preview_question(
    project_id: UUID,
    code: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project, question = repository.question(project_id, code)
        return calculate_preview(
            repository.source_path(project_id), question, project["inspection"]["variables"]
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вопрос не найден.") from exc
    except ToplineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{code}/base")
def assign_question_base(
    project_id: UUID,
    code: str,
    update: QuestionBaseUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.assign_question_base(project_id, code, update.filter_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Вопрос или фильтр не найдены.") from exc
