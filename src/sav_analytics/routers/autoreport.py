"""Автоотчёт (PQ.18, решение 034): бриф → план → сборка → правка → DOCX."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .. import ai_jobs
from ..api_dependencies import get_long_chat_model, get_repository
from ..assistant.models import ChatModel
from ..autoreport_jobs import run_build, run_plan
from ..core import autoreport
from ..core.docx_report import build_docx
from ..core.questionnaire import stored_questionnaire
from ..repository import ProjectNotFoundError, ProjectRepository
from .ai import ai_refusal

router = APIRouter(prefix="/api/projects/{project_id}/autoreport", tags=["autoreport"])

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class Brief(BaseModel):
    tasks: str = Field(default="", max_length=8000)
    description: str = Field(default="", max_length=8000)
    object: str = Field(default="", max_length=500)


class PlanSection(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=500)
    questions: list[str] = Field(min_length=1, max_length=20)


class PlanUpdate(BaseModel):
    banner: list[str] = Field(default_factory=list, max_length=10)
    weight: str | None = Field(default=None, max_length=64)
    sections: list[PlanSection] = Field(min_length=1, max_length=20)
    answers: dict[str, str] = Field(default_factory=dict)


class BuildRequest(BaseModel):
    # Правленные человеком блоки, которые можно перезаписать новым текстом.
    overwrite: list[str] = Field(default_factory=list, max_length=50)


class SectionPatch(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    text: str | None = Field(default=None, max_length=20_000)
    hidden: list[str] | None = Field(default=None, max_length=50)
    charts: dict[str, Literal["bar", "column"]] | None = None


class ReportPatch(BaseModel):
    summary: str | None = Field(default=None, max_length=20_000)
    conclusion: str | None = Field(default=None, max_length=20_000)
    sections: list[SectionPatch] = Field(default_factory=list, max_length=20)
    order: list[str] | None = Field(default=None, max_length=20)


def _directory(repository: ProjectRepository, project_id: UUID):
    try:
        project = repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    return project, repository.root / str(project_id)


def _view(
    state: dict[str, Any], project: dict[str, Any], directory: Path | None = None
) -> dict[str, Any]:
    labels = {item["code"]: item["label"] for item in project["configuration"]["questions"]}
    stored = stored_questionnaire(directory) if directory is not None else None
    return {
        "questionnaire": (
            {"filename": stored["filename"], "chars": stored["chars"]} if stored else None
        ),
        "wave_label": autoreport.wave_label(project),
        **state,
        "catalog": autoreport.plan_catalog(project),
        "weights": autoreport.weight_candidates(project),
        "wave_variable": autoreport.wave_variable(project),
        "labels": labels,
        "edited_blocks": autoreport.edited_blocks(state.get("report")),
        "stale": bool(state.get("report"))
        and state["report"].get("revision") != project["configuration"]["revision"],
    }


@router.get("")
def get_autoreport(
    project_id: UUID, repository: Annotated[ProjectRepository, Depends(get_repository)]
) -> dict:
    project, directory = _directory(repository, project_id)
    return _view(autoreport.load(directory), project, directory)


@router.put("/brief")
def put_brief(
    project_id: UUID,
    brief: Brief,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    project, directory = _directory(repository, project_id)
    state = autoreport.load(directory)
    state["brief"] = brief.model_dump()
    autoreport.save(directory, state)
    return _view(state, project, directory)


@router.post("/plan", response_model=None)
def start_plan(
    project_id: UUID,
    request: Request,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    model: Annotated[ChatModel | None, Depends(get_long_chat_model)],
) -> dict | JSONResponse:
    project, _directory_path = _directory(repository, project_id)
    refusal = ai_refusal(request, project, model)
    if refusal is not None:
        return refusal
    assert model is not None
    return ai_jobs.start_job(
        str(project_id),
        "autoreport_plan",
        "План автоотчёта",
        lambda progress: run_plan(repository, project_id, model, progress),
        subject="autoreport",
    )


@router.put("/plan")
def put_plan(
    project_id: UUID,
    update: PlanUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """План после правки человеком и ответы на уточнения."""
    project, directory = _directory(repository, project_id)
    state = autoreport.load(directory)
    plan, warnings = autoreport.checked_plan(
        project,
        {
            "banner": update.banner,
            "weight": update.weight or "",
            "sections": [section.model_dump() for section in update.sections],
            "notes": (state.get("plan") or {}).get("notes", ""),
        },
    )
    if not plan["sections"]:
        raise HTTPException(status_code=422, detail="В плане нет ни одного раздела с вопросами.")
    known = {item["id"] for item in state["clarifications"]}
    state["plan"] = plan
    state["answers"] = {
        key: value.strip()[:2000] for key, value in update.answers.items() if key in known
    }
    autoreport.save(directory, state)
    return {**_view(state, project, directory), "warnings": warnings}


@router.post("/build", response_model=None)
def start_build(
    project_id: UUID,
    body: BuildRequest,
    request: Request,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    model: Annotated[ChatModel | None, Depends(get_long_chat_model)],
) -> dict | JSONResponse:
    project, directory = _directory(repository, project_id)
    refusal = ai_refusal(request, project, model)
    if refusal is not None:
        return refusal
    assert model is not None
    if not (autoreport.load(directory).get("plan") or {}).get("sections"):
        raise HTTPException(status_code=422, detail="Сначала составьте план отчёта.")
    return ai_jobs.start_job(
        str(project_id),
        "autoreport_build",
        "Сборка автоотчёта",
        lambda progress: run_build(repository, project_id, model, body.overwrite, progress),
        subject="autoreport",
    )


@router.put("/report")
def patch_report(
    project_id: UUID,
    patch: ReportPatch,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Правка собранного отчёта. Изменённый текст помечается правкой
    человека: пересборка спросит, перезаписывать ли его."""
    project, directory = _directory(repository, project_id)
    state = autoreport.load(directory)
    report = state.get("report")
    if not report:
        raise HTTPException(status_code=404, detail="Отчёт ещё не собран.")
    for key in ("summary", "conclusion"):
        text = getattr(patch, key)
        if text is not None and text != report[key]["text"]:
            report[key] = {"text": text, "edited": True, "warnings": []}
    sections = {section["id"]: section for section in report["sections"]}
    for item in patch.sections:
        section = sections.get(item.id)
        if section is None:
            raise HTTPException(status_code=404, detail="Раздел не найден.")
        if item.text is not None and item.text != section["text"]:
            section.update(text=item.text, edited=True, warnings=[])
        for card in section["cards"]:
            if item.hidden is not None:
                card["hidden"] = card["code"] in set(item.hidden)
            if item.charts and card["code"] in item.charts:
                card["chart"] = item.charts[card["code"]]
    if patch.order is not None:
        if set(patch.order) != set(sections) or len(patch.order) != len(sections):
            raise HTTPException(status_code=422, detail="Порядок должен содержать все разделы.")
        report["sections"] = [sections[identifier] for identifier in patch.order]
        if state.get("plan"):
            plan_sections = {item["id"]: item for item in state["plan"]["sections"]}
            state["plan"]["sections"] = [
                plan_sections[identifier] for identifier in patch.order
                if identifier in plan_sections
            ]
    autoreport.save(directory, state)
    return _view(state, project, directory)


@router.get("/report.docx")
def download_docx(
    project_id: UUID, repository: Annotated[ProjectRepository, Depends(get_repository)]
) -> Response:
    project, directory = _directory(repository, project_id)
    state = autoreport.load(directory)
    if not state.get("report"):
        raise HTTPException(status_code=404, detail="Отчёт ещё не собран.")
    content = build_docx(state["report"], state["brief"], project["name"])
    filename = f"{project['name']} — отчёт.docx"
    return Response(
        content,
        media_type=DOCX_TYPE,
        headers={
            "Content-Disposition": "attachment; filename=report.docx; "
            f"filename*=UTF-8''{quote(filename)}"
        },
    )
