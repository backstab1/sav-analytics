from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .core.models import QuestionType, VariableRole

# 1 — настройки расчёта хранились на каждом баннере.
# 2 — они живут только в configuration.report_settings, баннер несёт название и блоки.
CONFIGURATION_SCHEMA_VERSION = 2


class InvalidStoredProjectError(ValueError):
    pass


class _StoredModel(BaseModel):
    # Forward-compatible readers preserve fields introduced by newer application code.
    model_config = ConfigDict(extra="allow")


class StoredSource(_StoredModel):
    size: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StoredQuestion(_StoredModel):
    code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1)
    question_type: QuestionType
    role: VariableRole
    source_variables: list[str] = Field(min_length=1)
    valid_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    included_in_report: bool


class StoredConfigurationEntity(_StoredModel):
    id: UUID


class StoredReportSettings(_StoredModel):
    compare_to_total: bool
    compare_target: Literal["rest", "total"]
    compare_pairwise: bool
    confidence_level: float = Field(gt=0, lt=1)
    bonferroni: bool
    # Со значением по умолчанию, а не обязательным полем: проекты, сохранённые
    # до появления настройки, обязаны открываться без миграции и без бампа
    # `schema_version` — форма файла не менялась, к ней добавился ключ.
    show_p_values: bool = False
    minimum_base: int = Field(ge=1, le=100_000)
    weight_variable: str | None
    calculated_weight_id: UUID | None
    wave_comparison: Literal["none", "previous", "control"]
    wave_control_value: str | int | float | None


class StoredConfiguration(_StoredModel):
    # Диапазон, а не точный Literal: код версии N обязан открывать файлы всех
    # прежних версий, иначе откат приложения делает уже мигрированные проекты
    # нечитаемыми. Файл новее самого кода читать нечем — его отвергаем.
    schema_version: int = Field(ge=1, le=CONFIGURATION_SCHEMA_VERSION)
    structure_version: int = Field(ge=1)
    revision: int = Field(ge=1)
    questions: list[StoredQuestion]
    recodings: list[StoredConfigurationEntity]
    banners: list[StoredConfigurationEntity]
    filters: list[StoredConfigurationEntity]
    calculated_weights: list[StoredConfigurationEntity]
    report_banner_id: UUID | None
    report_filter_id: UUID | None
    report_settings: StoredReportSettings
    updated_at: datetime


class StoredProject(_StoredModel):
    id: UUID
    name: str = Field(min_length=1)
    created_at: datetime
    original_filename: str = Field(min_length=1)
    source: StoredSource
    inspection: dict[str, Any]
    configuration: StoredConfiguration


def validate_stored_project(project: dict[str, Any]) -> None:
    try:
        StoredProject.model_validate(project)
    except ValidationError as exc:
        raise InvalidStoredProjectError("Сохранённая конфигурация проекта повреждена.") from exc
