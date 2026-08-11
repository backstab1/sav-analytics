from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..api_dependencies import get_repository
from ..api_schemas import ReportSettingsDefinition
from ..core.report_settings import ReportSettingsError, validate_report_settings
from ..repository import ProjectNotFoundError, ProjectRepository

router = APIRouter(prefix="/api/projects/{project_id}", tags=["report-settings"])


@router.put("/report-settings")
def update_report_settings(
    project_id: UUID,
    definition: ReportSettingsDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_report_settings(payload, project)
        return repository.update_report_settings(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except ReportSettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
