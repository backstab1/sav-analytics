"""Открытые ответы (PQ.17): кодификаторы, кодирование моделью, правка человеком."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from .. import ai_jobs
from ..api_dependencies import get_fast_chat_model, get_long_chat_model, get_repository
from ..api_presentation import ProjectRoute, present
from ..api_schemas import (
    CodeframeAnswerCodes,
    CodeframeBatch,
    CodeframeCodeRequest,
    CodeframeCreate,
    CodeframeRevise,
    CodeframeUpdate,
)
from ..assistant.models import ChatModel
from ..coding_jobs import run_coding, run_revision
from ..core.configuration_integrity import ConfigurationIntegrityError
from ..core.formulas import read_project_frame
from ..core.open_text import (
    WORDY_SHARE,
    CodeframeError,
    answer_rows,
    codeframe_summary,
    codeframe_text_variable,
    looks_like_service_field,
    text_profile,
)
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository
from .ai import ai_refusal

router = APIRouter(
    prefix="/api/projects/{project_id}/codeframes", tags=["codeframes"], route_class=ProjectRoute
)

_NOT_FOUND = "Кодификатор не найден."


def _texts(repository: ProjectRepository, project_id: UUID, codeframe_id: UUID):
    project = repository.get(project_id)
    codeframe = repository._find_codeframe(project, codeframe_id)
    variable = codeframe_text_variable(codeframe, project)
    frame = read_project_frame(repository.source_path(project_id), project, [variable])
    return frame[variable], codeframe, repository.coding(project_id, codeframe)


def _project(repository: ProjectRepository, project_id: UUID) -> dict:
    try:
        return repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc


@router.get("/candidates")
def candidates(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """«Распознать открытые»: текстовые вопросы проекта, включая поля
    «Другое», сначала похожие на ответы респондентов, потом служебные."""
    project = _project(repository, project_id)
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
            "service": looks_like_service_field(item["code"], item["label"]),
            **text_profile(frame[item["source_variables"][0]]),
        }
        for item in questions
    ]
    for item in result:
        item["respondent_answers"] = (
            not item["service"] and item["wordy_share"] >= WORDY_SHARE and item["answered"] > 0
        )
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


def _start_coding(
    repository: ProjectRepository,
    project_id: UUID,
    codeframe: dict,
    mode: str,
    main: ChatModel,
    fast: ChatModel,
    question_label: str,
) -> dict[str, Any]:
    codeframe_id = UUID(codeframe["id"])
    titles = {"new": "Кодирование", "keep_edits": "Перекодирование", "reset": "Кодирование заново"}
    return ai_jobs.start_job(
        str(project_id),
        "coding",
        f"{titles[mode]}: {codeframe['question_code']} — {question_label[:60]}",
        lambda progress: run_coding(
            repository, project_id, codeframe_id, mode, main, fast, progress
        ),
        subject=codeframe["id"],
    )


@router.post("/batch", response_model=None)
def code_questions(
    project_id: UUID,
    body: CodeframeBatch,
    request: Request,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    main: Annotated[ChatModel | None, Depends(get_long_chat_model)],
    fast: Annotated[ChatModel | None, Depends(get_fast_chat_model)],
) -> dict | JSONResponse:
    """Отмеченные открытые вопросы — в обработку: кодификаторы одной
    ревизией и по фоновой задаче кодирования на каждый."""
    project = _project(repository, project_id)
    refusal = ai_refusal(request, project, main)
    if refusal is not None:
        return refusal
    assert main is not None and fast is not None
    try:
        project = repository.create_codeframes(project_id, body.question_codes)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Вопрос не найден.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    labels = {item["code"]: item["label"] for item in project["configuration"]["questions"]}
    jobs = [
        _start_coding(
            repository, project_id, codeframe, "new", main, fast,
            labels.get(codeframe["question_code"], ""),
        )
        for codeframe in project["configuration"]["codeframes"]
        if codeframe["question_code"] in set(body.question_codes)
    ]
    return {"project": present(project), "jobs": jobs}


@router.post("/{codeframe_id}/code", response_model=None)
def code_answers(
    project_id: UUID,
    codeframe_id: UUID,
    body: CodeframeCodeRequest,
    request: Request,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    main: Annotated[ChatModel | None, Depends(get_long_chat_model)],
    fast: Annotated[ChatModel | None, Depends(get_fast_chat_model)],
) -> dict | JSONResponse:
    project = _project(repository, project_id)
    refusal = ai_refusal(request, project, main)
    if refusal is not None:
        return refusal
    assert main is not None and fast is not None
    try:
        codeframe = repository._find_codeframe(project, codeframe_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    labels = {item["code"]: item["label"] for item in project["configuration"]["questions"]}
    return _start_coding(
        repository, project_id, codeframe, body.mode, main, fast,
        labels.get(codeframe["question_code"], ""),
    )


@router.post("/{codeframe_id}/revise", response_model=None)
def revise_codebook(
    project_id: UUID,
    codeframe_id: UUID,
    body: CodeframeRevise,
    request: Request,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    main: Annotated[ChatModel | None, Depends(get_long_chat_model)],
) -> dict | JSONResponse:
    """Правка справочника моделью по просьбе: результат — черновик в задаче."""
    project = _project(repository, project_id)
    refusal = ai_refusal(request, project, main)
    if refusal is not None:
        return refusal
    assert main is not None
    try:
        codeframe = repository._find_codeframe(project, codeframe_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    return ai_jobs.start_job(
        str(project_id),
        "codebook_revision",
        f"Правка справочника: {codeframe['question_code']}",
        lambda progress: run_revision(
            repository, project_id, codeframe_id, body.request.strip(), main, progress
        ),
        subject=codeframe["id"],
    )


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
            {
                "instruction": request.instruction,
                "multi": request.multi,
                "other_threshold": request.other_threshold,
            },
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или кодификатор не найдены.") from exc
    except (InvalidUploadError, ConfigurationIntegrityError, CodeframeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{codeframe_id}/answers")
def set_answer_codes(
    project_id: UUID,
    codeframe_id: UUID,
    request: CodeframeAnswerCodes,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.set_answer_codes(project_id, codeframe_id, request.key, request.codes)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Кодификатор или код не найдены.") from exc
    except (InvalidUploadError, ConfigurationIntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{codeframe_id}/export")
def export_codeframe(
    project_id: UUID,
    codeframe_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Справочник и словарь правок для другого проекта.

    Словарь — правки человека «текст → коды»: в другом массиве они
    применятся к тем же ответам без обращения к модели.
    """
    try:
        project = repository.get(project_id)
        codeframe = repository._find_codeframe(project, codeframe_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    coding = repository.coding(project_id, codeframe)
    return {
        "format": "sav-analytics/codeframe",
        "version": 2,
        "label": codeframe["label"],
        "instruction": codeframe.get("instruction", ""),
        "themes": [
            {
                "id": theme["id"],
                "name": theme["name"],
                "parent_id": theme.get("parent_id"),
                "description": theme.get("description", ""),
            }
            for theme in codeframe["themes"]
        ],
        "dictionary": coding["dictionary"],
    }


@router.post("/{codeframe_id}/import")
def import_codeframe(
    project_id: UUID,
    codeframe_id: UUID,
    definition: dict,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    if definition.get("format") != "sav-analytics/codeframe" or not isinstance(
        definition.get("themes"), list
    ):
        raise HTTPException(status_code=422, detail="Это не файл кодификатора sav-analytics.")
    try:
        return repository.import_codeframe(project_id, codeframe_id, definition)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{codeframe_id}")
def delete_codeframe(
    project_id: UUID,
    codeframe_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_codeframe(project_id, codeframe_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except ConfigurationIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{codeframe_id}/summary")
def summary(
    project_id: UUID,
    codeframe_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        texts, codeframe, coding = _texts(repository, project_id, codeframe_id)
        return codeframe_summary(texts, codeframe, coding)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except CodeframeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{codeframe_id}/answers")
def answers(
    project_id: UUID,
    codeframe_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    theme_id: str | None = None,
    view: Annotated[str, Query(pattern="^(all|uncoded|low|dictionary|ai)$")] = "all",
    search: Annotated[str, Query(max_length=200)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    try:
        texts, codeframe, coding = _texts(repository, project_id, codeframe_id)
        return answer_rows(
            texts,
            codeframe,
            coding,
            theme_id=theme_id,
            view=view,
            search=search,
            offset=offset,
            limit=limit,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except CodeframeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
