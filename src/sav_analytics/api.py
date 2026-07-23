from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator

from .core.banner import BannerError, calculate_banner_preview, validate_banner
from .core.filtering import FilterError, calculate_filter_preview, validate_filter
from .core.models import QuestionType, VariableRole
from .core.recoding import RecodingError, calculate_recode_preview, validate_recode
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
    special_values: list[str | int | float] | None = None
    special_items: list[str] | None = None


class QuestionOrder(BaseModel):
    codes: list[str]


class RangeCategory(BaseModel):
    label: str = Field(min_length=1, max_length=250)
    lower: float | None = None
    upper: float | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> RangeCategory:
        if self.lower is None and self.upper is None:
            raise ValueError("Укажите хотя бы одну границу диапазона.")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("Нижняя граница не может быть выше верхней.")
        return self


class NumericRecodeDefinition(BaseModel):
    mode: Literal["ranges"] = "ranges"
    code: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=500)
    source_variable: str = Field(min_length=1, max_length=64)
    categories: list[RangeCategory] = Field(min_length=2, max_length=100)


class CategoryGroup(BaseModel):
    label: str = Field(min_length=1, max_length=250)
    values: list[str | int | float] = Field(min_length=1, max_length=500)


class CategoricalRecodeDefinition(BaseModel):
    mode: Literal["categories"]
    code: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=500)
    source_variable: str = Field(min_length=1, max_length=64)
    categories: list[CategoryGroup] = Field(min_length=2, max_length=100)


RecodeDefinition = NumericRecodeDefinition | CategoricalRecodeDefinition


class BannerSource(BaseModel):
    kind: Literal["question", "recoding"]
    ref: str = Field(min_length=1, max_length=64)


class BannerBlock(BaseModel):
    label: str | None = Field(default=None, max_length=250)
    sources: list[BannerSource] = Field(min_length=1, max_length=2)


class BannerDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    blocks: list[BannerBlock] = Field(min_length=1, max_length=50)


class FilterSource(BaseModel):
    kind: Literal["question", "recoding"]
    ref: str = Field(min_length=1, max_length=64)


class FilterCondition(BaseModel):
    kind: Literal["condition"] = "condition"
    source: FilterSource
    operator: Literal[
        "eq",
        "ne",
        "in",
        "not_in",
        "gt",
        "lt",
        "between",
        "filled",
        "missing",
        "selected",
        "selected_any",
        "selected_all",
        "selected_none",
    ]
    values: list[str | int | float] = Field(default_factory=list, max_length=500)
    lower: float | None = None
    upper: float | None = None


class FilterGroup(BaseModel):
    kind: Literal["group"] = "group"
    operator: Literal["and", "or"] = "and"
    items: list[FilterCondition | FilterGroup] = Field(min_length=1, max_length=50)


class FilterDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    rule: FilterGroup


class QuestionBaseUpdate(BaseModel):
    filter_id: UUID | None = None


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


@app.post("/api/projects/{project_id}/structure/refresh")
def refresh_structure(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.refresh_structure(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден."
        ) from exc
    except SavReadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
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
    except ToplineError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
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


@app.post("/api/projects/{project_id}/recodings", status_code=status.HTTP_201_CREATED)
def create_recoding(
    project_id: UUID,
    definition: RecodeDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_recode(payload, project["inspection"]["variables"])
        return repository.create_recoding(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден."
        ) from exc
    except (RecodingError, InvalidUploadError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.put("/api/projects/{project_id}/recodings/{recoding_id}")
def update_recoding(
    project_id: UUID,
    recoding_id: UUID,
    definition: RecodeDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_recode(payload, project["inspection"]["variables"])
        return repository.update_recoding(project_id, recoding_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект или перекодировка не найдены."
        ) from exc
    except (RecodingError, InvalidUploadError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.delete("/api/projects/{project_id}/recodings/{recoding_id}")
def delete_recoding(
    project_id: UUID,
    recoding_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_recoding(project_id, recoding_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект или перекодировка не найдены."
        ) from exc


@app.get("/api/projects/{project_id}/recodings/{recoding_id}/preview")
def preview_recoding(
    project_id: UUID,
    recoding_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project, recoding = repository.recoding(project_id, recoding_id)
        validate_recode(recoding, project["inspection"]["variables"])
        return calculate_recode_preview(repository.source_path(project_id), recoding)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект или перекодировка не найдены."
        ) from exc
    except RecodingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.post("/api/projects/{project_id}/banners", status_code=status.HTTP_201_CREATED)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден."
        ) from exc
    except BannerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.put("/api/projects/{project_id}/banners/{banner_id}")
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект или баннер не найдены."
        ) from exc
    except BannerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.delete("/api/projects/{project_id}/banners/{banner_id}")
def delete_banner(
    project_id: UUID,
    banner_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_banner(project_id, banner_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект или баннер не найдены."
        ) from exc


@app.get("/api/projects/{project_id}/banners/{banner_id}/preview")
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект или баннер не найдены."
        ) from exc
    except BannerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.post("/api/projects/{project_id}/filters", status_code=status.HTTP_201_CREATED)
def create_filter(
    project_id: UUID,
    definition: FilterDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_filter(payload, project)
        return repository.create_filter(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/projects/{project_id}/filters/{filter_id}")
def update_filter(
    project_id: UUID,
    filter_id: UUID,
    definition: FilterDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        validate_filter(payload, project)
        return repository.update_filter(project_id, filter_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или фильтр не найдены.") from exc
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/projects/{project_id}/filters/{filter_id}")
def delete_filter(
    project_id: UUID,
    filter_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_filter(project_id, filter_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или фильтр не найдены.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/filters/{filter_id}/preview")
def preview_filter(
    project_id: UUID,
    filter_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project, definition = repository.filter(project_id, filter_id)
        return calculate_filter_preview(repository.source_path(project_id), definition, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или фильтр не найдены.") from exc
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/projects/{project_id}/questions/{code}/base")
def assign_question_base(
    project_id: UUID,
    code: str,
    update: QuestionBaseUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.assign_question_base(project_id, code, update.filter_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Вопрос или фильтр не найдены.") from exc


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
