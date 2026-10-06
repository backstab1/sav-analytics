"""Фоновые задачи автоотчёта (PQ.18): план по брифу и сборка отчёта."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .ai_jobs import JobFailure, Progress
from .assistant.autoreport import request_plan, request_texts
from .assistant.models import ChatModel, ModelError
from .core import autoreport
from .core.reporting.live import build_live_table
from .core.reporting.models import ReportError
from .core.weight_validation import WeightNotUsableError, ensure_project_weight_usable
from .repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository


def _project(repository: ProjectRepository, project_id: UUID) -> dict:
    try:
        return repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise JobFailure("Проект удалён.") from exc


def run_plan(
    repository: ProjectRepository, project_id: UUID, model: ChatModel, progress: Progress
) -> dict[str, Any]:
    """Модель предлагает план и уточняющие вопросы; план пишется черновиком."""
    progress(0, 1, "Модель составляет план отчёта")
    project = _project(repository, project_id)
    directory = repository.root / str(project_id)
    state = autoreport.load(directory)
    try:
        proposal = request_plan(
            model,
            state["brief"],
            state["clarifications"],
            state["answers"],
            autoreport.plan_catalog(project),
            autoreport.weight_candidates(project),
            autoreport.wave_variable(project),
        )
    except ModelError as exc:
        raise JobFailure(f"Модель не ответила: {exc}", "AI_PROVIDER_ERROR") from exc
    plan, clarifications, warnings = autoreport.validate_plan(project, proposal)
    if not plan["sections"]:
        raise JobFailure("Модель не предложила ни одного раздела с вопросами массива.")
    state = autoreport.load(directory)
    state["plan"] = plan
    # Отвеченные уточнения остаются: модель их уже учла.
    answered = [item for item in state["clarifications"] if state["answers"].get(item["id"])]
    offset = len(answered)
    state["clarifications"] = answered + [
        {**item, "id": f"q{offset + index + 1}"} for index, item in enumerate(clarifications)
    ]
    autoreport.save(directory, state)
    return {"sections": len(plan["sections"]), "warnings": warnings,
            "clarifications": len(clarifications)}


def run_build(
    repository: ProjectRepository,
    project_id: UUID,
    model: ChatModel,
    overwrite: list[str],
    progress: Progress,
) -> dict[str, Any]:
    """Применить план к проекту, посчитать разделы ядром и написать текст."""
    directory = repository.root / str(project_id)
    state = autoreport.load(directory)
    plan = state.get("plan")
    if not plan or not plan.get("sections"):
        raise JobFailure("Сначала составьте план отчёта.")
    total_steps = len(plan["sections"]) + 3
    progress(0, total_steps, "Настраиваем разрез и вес")
    project = _project(repository, project_id)
    source = repository.source_path(project_id)
    if plan.get("weight"):
        try:
            ensure_project_weight_usable(source, plan["weight"], project)
        except WeightNotUsableError as exc:
            raise JobFailure(f"Вес {plan['weight']} не годится: {exc}") from exc
    try:
        project, banner_id = repository.apply_autoreport_setup(
            project_id, plan["banner"], plan.get("weight")
        )
    except (ProjectNotFoundError, InvalidUploadError) as exc:
        raise JobFailure(f"План не применился к проекту: {exc}") from exc

    tables: dict[str, list[dict[str, Any]]] = {}
    first_live = None
    for index, section in enumerate(plan["sections"], start=1):
        progress(index, total_steps, f"Считаем раздел «{section['title']}»")
        try:
            live = build_live_table(
                source, project, questions=section["questions"], banner_id=banner_id
            )
        except ReportError as exc:
            raise JobFailure(f"Раздел «{section['title']}» не посчитался: {exc}") from exc
        first_live = first_live or live
        tables[section["id"]] = autoreport.table_numbers(live)

    progress(total_steps - 2, total_steps, "Модель пишет выводы")
    sections_for_model = [
        {
            "id": section["id"],
            "title": section["title"],
            "goal": section["goal"],
            "tables": autoreport.numbers_for_model(tables[section["id"]]),
        }
        for section in plan["sections"]
    ]
    try:
        texts = request_texts(
            model, state["brief"], state["clarifications"], state["answers"], sections_for_model
        )
    except ModelError as exc:
        raise JobFailure(f"Модель не ответила: {exc}", "AI_PROVIDER_ERROR") from exc

    progress(total_steps - 1, total_steps, "Собираем отчёт")
    state = autoreport.load(directory)
    state["report"] = autoreport.assemble_report(
        state.get("report"),
        plan,
        tables,
        texts,
        autoreport.method_text(project, first_live, plan),
        int(project["configuration"]["revision"]),
        set(overwrite),
    )
    autoreport.save(directory, state)
    warnings = sum(len(section["warnings"]) for section in state["report"]["sections"])
    return {"sections": len(plan["sections"]), "unverified_numbers": warnings}
