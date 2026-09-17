from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import CodeframeCreate, CodeframeMark, CodeframeUpdate
from ..core.configuration_integrity import ConfigurationIntegrityError
from ..core.formulas import read_project_frame
from ..core.open_text import (
    WORDY_SHARE,
    CodeframeError,
    answer_rows,
    codeframe_summary,
    codeframe_text_variable,
    text_profile,
)
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}/codeframes", tags=["codeframes"], route_class=ProjectRoute
)


def _texts(repository: ProjectRepository, project_id: UUID, codeframe_id: UUID):
    project = repository.get(project_id)
    codeframe = repository._find_codeframe(project, codeframe_id)
    variable = codeframe_text_variable(codeframe, project)
    frame = read_project_frame(repository.source_path(project_id), project, [variable])
    return frame[variable], codeframe


@router.get("/candidates")
def candidates(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Открытые вопросы проекта: сначала похожие на ответы, потом служебные поля."""
    try:
        project = repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    questions = [
        item
        for item in project["configuration"]["questions"]
        if item["question_type"] == "open_text" and len(item.get("source_variables") or []) == 1
    ]
    if not questions:
        return {"questions": []}
    columns = [item["source_variables"][0] for item in questions]
    frame = read_project_frame(repository.source_path(project_id), project, columns)
    framed = {item["question_code"] for item in project["configuration"].get("codeframes", [])}
    result = [
        {
            "code": item["code"],
            "label": item["label"],
            "has_codeframe": item["code"] in framed,
            **text_profile(frame[item["source_variables"][0]]),
        }
        for item in questions
    ]
    result.sort(key=lambda item: (not item["has_codeframe"], -item["wordy_share"]))
    return {"questions": result, "wordy_share": WORDY_SHARE}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_codeframe(
    project_id: UUID,
    request: CodeframeCreate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.create_codeframe(project_id, request.question_code)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вопрос не найдены.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{codeframe_id}")
def update_codeframe(
    project_id: UUID,
    codeframe_id: UUID,
    request: CodeframeUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.update_codeframe(
            project_id,
            codeframe_id,
            request.label,
            [theme.model_dump() for theme in request.themes],
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или кодификатор не найдены.") from exc
    except (InvalidUploadError, ConfigurationIntegrityError, CodeframeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{codeframe_id}/marks")
def mark_answer(
    project_id: UUID,
    codeframe_id: UUID,
    request: CodeframeMark,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.mark_codeframe_answer(
            project_id, codeframe_id, request.theme_id, request.row, request.value
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Кодификатор или тема не найдены.") from exc


@router.delete("/{codeframe_id}")
def delete_codeframe(
    project_id: UUID,
    codeframe_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_codeframe(project_id, codeframe_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Кодификатор не найден.") from exc
    except ConfigurationIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{codeframe_id}/summary")
def summary(
    project_id: UUID,
    codeframe_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        texts, codeframe = _texts(repository, project_id, codeframe_id)
        return codeframe_summary(texts, codeframe)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Кодификатор не найден.") from exc
    except CodeframeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{codeframe_id}/answers")
def answers(
    project_id: UUID,
    codeframe_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    theme_id: str | None = None,
    uncoded: bool = False,
    search: Annotated[str, Query(max_length=200)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    try:
        texts, codeframe = _texts(repository, project_id, codeframe_id)
        return answer_rows(
            texts,
            codeframe,
            theme_id=theme_id,
            uncoded=uncoded,
            search=search,
            offset=offset,
            limit=limit,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Кодификатор не найден.") from exc
    except CodeframeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
