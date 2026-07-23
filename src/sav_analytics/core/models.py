from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class QuestionType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_DICHOTOMY = "multiple_choice_dichotomy"
    MULTIPLE_CATEGORICAL = "multiple_choice_categorical"
    SCALE = "scale"
    NUMERIC = "numeric"
    RANKING = "ranking"
    MATRIX = "matrix"
    OPEN_TEXT = "open_text"
    TECHNICAL = "technical"


class VariableRole(StrEnum):
    QUESTION = "question"
    ID = "id"
    WEIGHT = "weight"
    WAVE = "wave"
    FILTER = "filter"
    TECHNICAL = "technical"


@dataclass(slots=True)
class ValueLabel:
    value: Any
    label: str


@dataclass(slots=True)
class VariableInspection:
    name: str
    label: str
    storage_type: str
    original_format: str | None
    measurement_level: str | None
    question_type: QuestionType
    role: VariableRole
    valid_count: int
    missing_count: int
    unique_count: int
    value_labels: list[ValueLabel] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QuestionInspection:
    code: str
    label: str
    question_type: QuestionType
    role: VariableRole
    source_variables: list[str]
    valid_count: int
    missing_count: int
    included_in_report: bool
    recognition: str = "auto"
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SavInspection:
    row_count: int
    variable_count: int
    variables: list[VariableInspection]
    questions: list[QuestionInspection]
    multiple_response_sets: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
