from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .core.banner import BannerError, calculate_banner_preview, validate_banner
from .core.filtering import FilterError, calculate_filter_preview, validate_filter
from .core.models import QuestionType, VariableRole
from .core.recoding import RecodingError, calculate_recode_preview, validate_recode
from .core.report import ReportError, build_statistics_txt, build_topline_xlsx
from .core.sav_reader import SavReadError
from .core.topline import ToplineError, calculate_preview
from .core.weighting import WeightingError, build_raking_export, calculate_raking_preview
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
    special_metric: Literal["none", "nps", "csat"] | None = None


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
    compare_to_total: bool = False
    compare_pairwise: bool = False
    confidence_level: float = Field(default=0.95, gt=0, lt=1)
    bonferroni: bool = False
    minimum_base: int = Field(default=30, ge=1, le=100_000)
    weight_variable: str | None = Field(default=None, max_length=64)
    calculated_weight_id: UUID | None = None
    wave_comparison: Literal["none", "previous", "control"] = "none"
    wave_control_value: str | int | float | None = None

    @model_validator(mode="after")
    def validate_weight_selection(self) -> BannerDefinition:
        if self.weight_variable and self.calculated_weight_id:
            raise ValueError("Выберите готовый или рассчитанный вес, но не оба сразу.")
        if self.wave_comparison == "control" and self.wave_control_value is None:
            raise ValueError("Для контрольного сравнения выберите контрольную волну.")
        return self


class ReportBannerUpdate(BaseModel):
    banner_id: UUID | None = None


class WeightTarget(BaseModel):
    label: str = Field(min_length=1, max_length=250)
    values: list[str | int | float] = Field(min_length=1, max_length=500)
    percent: float = Field(gt=0, le=100)


class WeightDimension(BaseModel):
    variable: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=500)
    targets: list[WeightTarget] = Field(min_length=2, max_length=100)


class CalculatedWeightDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    dimensions: list[WeightDimension] = Field(min_length=1, max_length=20)
    lower_bound: float | None = Field(default=0.3, gt=0)
    upper_bound: float | None = Field(default=3.0, gt=0)
    tolerance: float = Field(default=0.001, gt=0, lt=1)
    maximum_iterations: int = Field(default=500, ge=1, le=5000)

    @model_validator(mode="after")
    def validate_limits(self) -> CalculatedWeightDefinition:
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound >= self.upper_bound
        ):
            raise ValueError("Нижняя граница веса должна быть меньше верхней.")
        return self


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
    except (ToplineError, InvalidUploadError) as exc:
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
    except ToplineError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
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
    except (BannerError, InvalidUploadError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@app.post("/api/projects/{project_id}/weights", status_code=status.HTTP_201_CREATED)
def create_calculated_weight(
    project_id: UUID,
    definition: CalculatedWeightDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        repository.get(project_id)
        calculate_raking_preview(repository.source_path(project_id), payload)
        return repository.create_calculated_weight(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except (WeightingError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/projects/{project_id}/weights/{weight_id}")
def update_calculated_weight(
    project_id: UUID,
    weight_id: UUID,
    definition: CalculatedWeightDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        repository.get(project_id)
        calculate_raking_preview(repository.source_path(project_id), payload)
        return repository.update_calculated_weight(project_id, weight_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вес не найдены.") from exc
    except (WeightingError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/projects/{project_id}/weights/{weight_id}")
def delete_calculated_weight(
    project_id: UUID,
    weight_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.delete_calculated_weight(project_id, weight_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вес не найдены.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/weights/{weight_id}/preview")
def preview_calculated_weight(
    project_id: UUID,
    weight_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project, definition = repository.calculated_weight(project_id, weight_id)
        return calculate_raking_preview(
            repository.source_path(project_id), definition
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вес не найдены.") from exc
    except (WeightingError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/weights/{weight_id}/export.xlsx")
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


@app.put("/api/projects/{project_id}/report-banner")
def assign_report_banner(
    project_id: UUID,
    update: ReportBannerUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.assign_report_banner(project_id, update.banner_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Проект или баннер не найдены."
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


@app.post("/api/projects/{project_id}/filters/preview")
def preview_filter_draft(
    project_id: UUID,
    definition: FilterDefinition,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    payload = definition.model_dump(mode="json")
    try:
        project = repository.get(project_id)
        return calculate_filter_preview(repository.source_path(project_id), payload, project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
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


@app.put("/api/projects/{project_id}/report-filter")
def assign_report_filter(
    project_id: UUID,
    update: QuestionBaseUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        return repository.assign_report_filter(project_id, update.filter_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или фильтр не найдены.") from exc


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


@app.get("/api/projects/{project_id}/reports/topline.xlsx")
def download_topline(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> StreamingResponse:
    try:
        project = repository.get(project_id)
        content = build_topline_xlsx(repository.source_path(project_id), project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except (ReportError, BannerError, FilterError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = f"{project['name']}_topline.xlsx"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


@app.get("/api/projects/{project_id}/reports/statistics.txt")
def download_statistics(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> StreamingResponse:
    try:
        project = repository.get(project_id)
        content = build_statistics_txt(repository.source_path(project_id), project)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except (ReportError, BannerError, FilterError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = f"{project['name']}_statistics.txt"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
