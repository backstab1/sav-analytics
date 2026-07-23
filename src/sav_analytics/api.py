from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .core.models import QuestionType, VariableRole
from .core.sav_reader import SavReadError
from .core.topline import ToplineError, calculate_preview
from .repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository
from .settings import Settings


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_repository(settings: Annotated[Settings, Depends(get_settings)]) -> ProjectRepository:
    return ProjectRepository(settings.data_dir / "projects", settings.max_upload_bytes)


app = FastAPI(title="sav-analytics API", version="0.1.0")
logger = logging.getLogger(__name__)


class QuestionUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=5000)
    question_type: QuestionType | None = None
    role: VariableRole | None = None
    included_in_report: bool | None = None


class QuestionOrder(BaseModel):
    codes: list[str]


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unexpected API error on %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": (
                "Не удалось обработать SAV из-за внутренней ошибки. "
                "Подробности записаны в консоль сервера."
            )
        },
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/projects")
def list_projects(
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> list[dict]:
    return repository.list()


@app.post("/api/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()] = "",
) -> dict:
    try:
        return repository.create(name, file.filename or "upload.sav", file.file)
    except (InvalidUploadError, SavReadError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.get("/api/projects/{project_id}")
def get_project(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден."
        ) from exc


@app.patch("/api/projects/{project_id}/questions/{code}")
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект или вопрос не найден."
        ) from exc


@app.put("/api/projects/{project_id}/questions/order")
def reorder_questions(
    project_id: UUID,
    order: QuestionOrder,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.reorder_questions(project_id, order.codes)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден."
        ) from exc
    except InvalidUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.get("/api/projects/{project_id}/questions/{code}/preview")
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект или вопрос не найден."
        ) from exc
    except ToplineError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.get("/api/projects/{project_id}/source")
def download_source(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> FileResponse:
    try:
        project = repository.get(project_id)
        path = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден."
        ) from exc
    return FileResponse(
        path,
        media_type="application/x-spss-sav",
        filename=project["original_filename"],
    )


static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
