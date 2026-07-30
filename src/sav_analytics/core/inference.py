from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from .models import QuestionType, VariableRole

_ID_NAMES = re.compile(r"^(id|case_?id|respondent_?id|record_?id|serial)$", re.IGNORECASE)
_WEIGHT_NAMES = re.compile(r"^(weight|weights|wgt|ves|вес)$", re.IGNORECASE)
_TECHNICAL_NAMES = re.compile(
    r"^(start|end|duration|status|date|time|timestamp|sys_|technical_)", re.IGNORECASE
)
_DATE_FORMAT = re.compile(r"(DATE|TIME|DATETIME|WKDAY|MONTH|QYR)", re.IGNORECASE)
_SPECIAL_LABEL = re.compile(
    r"(затрудн|не\s*знаю|нет\s*ответа|без\s*ответа|отказ|"
    r"ничего\s*(из|не)|ни\s*один|другое\s*затруд)",
    re.IGNORECASE,
)


def infer_role(name: str, original_format: str | None) -> VariableRole:
    if _ID_NAMES.match(name):
        return VariableRole.ID
    if _WEIGHT_NAMES.match(name):
        return VariableRole.WEIGHT
    if _TECHNICAL_NAMES.match(name) or (original_format and _DATE_FORMAT.search(original_format)):
        return VariableRole.TECHNICAL
    return VariableRole.QUESTION


def infer_question_type(
    series: pd.Series,
    *,
    measurement_level: str | None,
    value_labels: dict[Any, str],
    role: VariableRole,
) -> tuple[QuestionType, list[str]]:
    warnings: list[str] = []
    if role is VariableRole.TECHNICAL or role is VariableRole.ID:
        return QuestionType.TECHNICAL, warnings

    valid = series.dropna()
    unique_count = int(valid.nunique(dropna=True))
    level = (measurement_level or "").lower()

    if not pd.api.types.is_numeric_dtype(series):
        if value_labels:
            return QuestionType.SINGLE_CHOICE, warnings
        return QuestionType.OPEN_TEXT, warnings

    if value_labels:
        if level == "scale" and _looks_like_short_scale(valid):
            return QuestionType.SCALE, warnings
        return QuestionType.SINGLE_CHOICE, warnings

    if level in {"nominal", "ordinal"} and 0 < unique_count <= 30:
        warnings.append("У категорий нет подписей значений; будут показаны исходные коды.")
        return QuestionType.SINGLE_CHOICE, warnings

    if _looks_like_short_scale(valid):
        warnings.append("Тип шкалы определён по диапазону значений и требует проверки.")
        return QuestionType.SCALE, warnings

    return QuestionType.NUMERIC, warnings


def _looks_like_short_scale(values: pd.Series) -> bool:
    if values.empty or not pd.api.types.is_numeric_dtype(values):
        return False
    unique = sorted(float(value) for value in values.unique())
    if len(unique) < 3 or len(unique) > 12:
        return False
    if any(not value.is_integer() for value in unique):
        return False
    return unique[-1] - unique[0] <= 11


def group_prefix(name: str) -> str | None:
    match = re.match(r"^(.+?)[_\.](\d+)$", name)
    return match.group(1) if match else None


def is_dichotomy(values: Iterable[Any]) -> bool:
    try:
        normalized = {float(value) for value in values if pd.notna(value)}
    except (TypeError, ValueError):
        return False
    return bool(normalized) and normalized <= {0.0, 1.0}


def is_special_label(label: str) -> bool:
    return bool(_SPECIAL_LABEL.search(label))
