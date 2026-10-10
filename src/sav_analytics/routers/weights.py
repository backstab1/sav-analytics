from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import CalculatedWeightDefinition, WeightTargetTemplateRequest
from ..core.configuration_integrity import ConfigurationIntegrityError
from ..core.weight_targets import WeightTargetError, build_target_template, read_target_file
from ..core.weight_validation import assess_project_weight, assess_weight_without_waves
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
    own_wave: Annotated[list[str] | None, Query()] = None,
) -> dict:
    """Разбор готового веса до его применения.

    Отдаёт и вердикт, и числа: `requirements.md` §8 требует показывать
    распределение веса перед применением, а не только сообщать об отказе.
    `own_wave` — волны со своим весом: их строки этот вес не взвешивает, и
    разбор идёт без них, как проверка при сохранении.
    """

    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    if own_wave:
        return assess_weight_without_waves(source, variable, project, own_wave).to_dict()
    return assess_project_weight(source, variable, project).to_dict()


@router.get("/comparison")
def compare_weights(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Все варианты веса проекта рядом: без веса, готовые и рассчитанные.

    Числа те же, что в диагностике каждого веса, поэтому вариант
    выбирается по эффективной базе и DEFF, а не по памяти. Вес, который
    не считается или не годится, остаётся строкой с причиной.
    """
    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    configuration = project["configuration"]
    settings = configuration.get("report_settings") or {}
    count = int(project["inspection"]["row_count"])
    rows: list[dict] = [
        {
            "kind": "none",
            "value": "",
            "name": "Без веса",
            "applied": not settings.get("weight_variable")
            and not settings.get("calculated_weight_id"),
            "usable": True,
            "error": None,
            "effective_base": float(count),
            "design_effect": 1.0,
            "efficiency_percent": 100.0,
            "minimum": 1.0,
            "maximum": 1.0,
            "maximum_deviation_pp": None,
        }
    ]
    for question in configuration["questions"]:
        if question.get("role") != "weight" or len(question.get("source_variables") or []) != 1:
            continue
        variable = question["source_variables"][0]
        assessment = assess_project_weight(source, variable, project)
        diagnostics = assessment.diagnostics
        rows.append(
            {
                "kind": "ready",
                "value": f"ready:{variable}",
                "name": variable,
                "applied": settings.get("weight_variable") == variable,
                "usable": assessment.usable,
                "error": " ".join(problem.message for problem in assessment.problems) or None,
                "effective_base": diagnostics.effective_base if diagnostics else None,
                "design_effect": diagnostics.design_effect if diagnostics else None,
                "efficiency_percent": diagnostics.efficiency_percent if diagnostics else None,
                "minimum": diagnostics.minimum if diagnostics else None,
                "maximum": diagnostics.maximum if diagnostics else None,
                "maximum_deviation_pp": None,
            }
        )
    for weight in configuration.get("calculated_weights", []):
        row = {
            "kind": "calculated",
            "value": f"calculated:{weight['id']}",
            "id": weight["id"],
            "name": weight["name"],
            "applied": settings.get("calculated_weight_id") == weight["id"],
            "usable": True,
            "error": None,
        }
        try:
            preview = calculate_raking_preview(source, weight, project)
        except (WeightingError, KeyError) as exc:
            row.update(usable=False, error=str(exc))
        else:
            row.update(
                {
                    key: preview[key]
                    for key in (
                        "effective_base",
                        "design_effect",
                        "efficiency_percent",
                        "minimum",
                        "maximum",
                        "maximum_deviation_pp",
                    )
                }
            )
        rows.append(row)
    return {"count": count, "rows": rows}


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
