from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import ProjectRename
from ..core.sav_reader import SavReadError
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects", tags=["projects"], route_class=ProjectRoute
)


@router.get("")
def list_projects(
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> list[dict]:
    return repository.list()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()] = "",
) -> dict:
    try:
        return repository.create(name, file.filename or "upload.sav", file.file)
    except (InvalidUploadError, SavReadError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Маршруты корзины стоят раньше «/{project_id}»: иначе слово «trash»
# проверялось бы как идентификатор проекта и получало отказ валидации.
@router.get("/trash")
def list_trash(
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> list[dict]:
    return repository.list_trash()


@router.post("/trash/{project_id}/restore")
def restore_project(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.restore(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проекта нет в корзине.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{project_id}")
def rename_project(
    project_id: UUID,
    update: ProjectRename,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.rename(project_id, update.name)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{project_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_project(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.duplicate(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc


@router.delete("/{project_id}")
def trash_project(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        repository.trash(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"trashed": str(project_id)}


@router.get("/{project_id}")
def get_project(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc


@router.post("/{project_id}/structure/refresh")
def refresh_structure(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.refresh_structure(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except SavReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{project_id}/source")
def download_source(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> FileResponse:
    try:
        project = repository.get(project_id)
        path = repository.source_path(project_id)
        original = repository.original_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    if original is not None:
        # Исходник — то, что загрузили: CSV, а не SAV, собранный из него.
        media_type = "text/tab-separated-values" if original.suffix == ".tsv" else "text/csv"
        return FileResponse(original, media_type=media_type, filename=project["original_filename"])
    return FileResponse(
        path,
        media_type="application/x-spss-sav",
        filename=project["original_filename"],
    )
