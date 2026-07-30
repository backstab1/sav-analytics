from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..api_dependencies import get_repository
from ..api_schemas import BannerDefinition, ReportBannerUpdate
from ..core.banner import BannerError, calculate_banner_preview, validate_banner
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(prefix="/api/projects/{project_id}", tags=["banners"])


@router.post("/banners", status_code=status.HTTP_201_CREATED)
def create_banner(
    project_id: UUID,
    definition: BannerDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_banner(payload, project)
        return repository.create_banner(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except (BannerError, InvalidUploadError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/banners/{banner_id}")
def update_banner(
    project_id: UUID,
    banner_id: UUID,
    definition: BannerDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_banner(payload, project)
        return repository.update_banner(project_id, banner_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или баннер не найдены.") from exc
    except BannerError as exc:
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
        return repository.assign_report_banner(project_id, update.banner_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или баннер не найдены.") from exc
