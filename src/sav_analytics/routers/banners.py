from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..api_dependencies import get_repository
from ..api_schemas import BannerDefinition, ReportBannerUpdate
from ..core.banner import BannerError, calculate_banner_preview, validate_banner
from ..core.report_settings import (
    REPORT_SETTING_KEYS,
    ReportSettingsError,
    validate_report_settings,
)
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(prefix="/api/projects/{project_id}", tags=["banners"])


@router.post("/banners", status_code=status.HTTP_201_CREATED)
def create_banner(
    project_id: UUID,
    definition: BannerDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    # exclude_unset keeps newly saved banners structural. Report-wide fields
    # remain accepted only so older API clients can be upgraded gradually.
    payload = definition.model_dump(mode="json", exclude_unset=True)
    try:
        project = repository.get(project_id)
        validate_banner(payload, project)
        prospective_settings = _prospective_report_settings(project, payload)
        prospective_banner = {"id": "pending", **payload}
        validate_report_settings(
            prospective_settings,
            _with_configuration(
                project,
                banners=[*project["configuration"]["banners"], prospective_banner],
                report_banner_id="pending",
            ),
        )
        return repository.create_banner(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except (BannerError, InvalidUploadError, ReportSettingsError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/banners/{banner_id}")
def update_banner(
    project_id: UUID,
    banner_id: UUID,
    definition: BannerDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json", exclude_unset=True)
    try:
        project = repository.get(project_id)
        validate_banner(payload, project)
        identifier = str(banner_id)
        banners = project["configuration"]["banners"]
        if not any(banner["id"] == identifier for banner in banners):
            raise ProjectNotFoundError(identifier)
        active = project["configuration"].get("report_banner_id") == identifier
        prospective_settings = (
            _prospective_report_settings(project, payload)
            if active
            else project["configuration"]["report_settings"]
        )
        validate_report_settings(
            prospective_settings,
            _with_configuration(
                project,
                banners=[
                    {"id": identifier, **payload} if banner["id"] == identifier else banner
                    for banner in banners
                ],
            ),
        )
        return repository.update_banner(project_id, banner_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или баннер не найдены.") from exc
    except (BannerError, ReportSettingsError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/banners/{banner_id}")
def delete_banner(
    project_id: UUID,
    banner_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_banner(project_id, banner_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или баннер не найдены.") from exc


@router.get("/banners/{banner_id}/preview")
def preview_banner(
    project_id: UUID,
    banner_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project, banner = repository.banner(project_id, banner_id)
        validate_banner(banner, project)
        return calculate_banner_preview(repository.source_path(project_id), banner, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или баннер не найдены.") from exc
    except BannerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/report-banner")
def assign_report_banner(
    project_id: UUID,
    update: ReportBannerUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project = repository.get(project_id)
        identifier = str(update.banner_id) if update.banner_id else None
        if identifier and not any(
            banner["id"] == identifier
            for banner in project["configuration"]["banners"]
        ):
            raise ProjectNotFoundError(identifier)
        validate_report_settings(
            project["configuration"]["report_settings"],
            _with_configuration(project, report_banner_id=identifier),
        )
        return repository.assign_report_banner(project_id, update.banner_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или баннер не найдены.") from exc
    except ReportSettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _prospective_report_settings(project: dict, banner: dict) -> dict:
    updates = {key: banner[key] for key in REPORT_SETTING_KEYS if key in banner}
    return {**project["configuration"]["report_settings"], **updates}


def _with_configuration(project: dict, **changes: object) -> dict:
    return {
        **project,
        "configuration": {**project["configuration"], **changes},
    }
