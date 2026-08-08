from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .core.models import QuestionType, VariableRole

CONFIGURATION_SCHEMA_VERSION = 1


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


class StoredConfiguration(_StoredModel):
    schema_version: Literal[1]
    structure_version: int = Field(ge=1)
    revision: int = Field(ge=1)
    questions: list[StoredQuestion]
    recodings: list[StoredConfigurationEntity]
    banners: list[StoredConfigurationEntity]
    filters: list[StoredConfigurationEntity]
    calculated_weights: list[StoredConfigurationEntity]
    report_banner_id: UUID | None
    report_filter_id: UUID | None
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
