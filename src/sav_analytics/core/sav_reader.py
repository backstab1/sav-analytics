from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat

from .inference import (
    group_prefix,
    infer_question_type,
    infer_role,
    is_dichotomy,
    is_special_label,
)
from .models import (
    QuestionInspection,
    QuestionType,
    SavInspection,
    ValueLabel,
    VariableInspection,
    VariableRole,
)


class SavReadError(ValueError):
    """Raised when a file cannot be interpreted as an SPSS SAV dataset."""


def inspect_sav(path: str | Path) -> SavInspection:
    source = Path(path)
    try:
        frame, metadata = pyreadstat.read_sav(
            source,
            apply_value_formats=False,
            user_missing=True,
            dates_as_pandas_datetime=False,
        )
    except Exception as exc:  # pyreadstat exposes several backend-specific errors
        raise SavReadError("Файл не удалось прочитать как корректный SPSS SAV.") from exc

    variables = [_inspect_variable(frame[name], name, metadata) for name in frame.columns]
    explicit_sets = _read_multiple_response_sets(metadata)
    questions, grouping_warnings = _build_questions(frame, variables, explicit_sets)
    warnings = grouping_warnings
    if not variables:
        warnings.append("В SAV не найдено ни одной переменной.")

    return SavInspection(
        row_count=len(frame),
        variable_count=len(frame.columns),
        variables=variables,
        questions=questions,
        multiple_response_sets=explicit_sets,
        warnings=warnings,
    )


def _inspect_variable(series: pd.Series, name: str, metadata: Any) -> VariableInspection:
    labels = getattr(metadata, "column_names_to_labels", {}) or {}
    value_labels_map = getattr(metadata, "variable_value_labels", {}) or {}
    formats = getattr(metadata, "original_variable_types", {}) or {}
    measures = getattr(metadata, "variable_measure", {}) or {}
    missing_ranges = getattr(metadata, "missing_ranges", {}) or {}
    missing_values = getattr(metadata, "missing_user_values", {}) or {}

    value_labels = value_labels_map.get(name, {}) or {}
    original_format = formats.get(name)
    measurement = measures.get(name)
    role = infer_role(name, original_format)

    metadata_warnings: list[str] = []
    missing_mask = series.isna().copy()
    for value in missing_values.get(name, []) or []:
        try:
            missing_mask |= series.eq(value)
        except (TypeError, ValueError):
            metadata_warnings.append(
                "Пользовательское пропущенное значение несовместимо с форматом переменной."
            )
    for value_range in missing_ranges.get(name, []) or []:
        lower = value_range.get("lo")
        upper = value_range.get("hi")
        if lower is not None and upper is not None:
            try:
                missing_mask |= series.between(lower, upper, inclusive="both")
            except (TypeError, ValueError):
                metadata_warnings.append(
                    "Диапазон пользовательских пропусков несовместим с форматом переменной."
                )

    analysis_series = series.mask(missing_mask)
    question_type, warnings = infer_question_type(
        analysis_series,
        measurement_level=measurement,
        value_labels=value_labels,
        role=role,
    )
    warnings.extend(metadata_warnings)
    storage_type = "numeric" if pd.api.types.is_numeric_dtype(series) else "string"

    return VariableInspection(
        name=name,
        label=str(labels.get(name) or name).strip(),
        storage_type=storage_type,
        original_format=original_format,
        measurement_level=measurement,
        question_type=question_type,
        role=role,
        valid_count=int((~missing_mask).sum()),
        missing_count=int(missing_mask.sum()),
        unique_count=int(analysis_series.nunique(dropna=True)),
        value_labels=[
            ValueLabel(value=_json_scalar(value), label=str(label))
            for value, label in value_labels.items()
        ],
        warnings=warnings,
    )


def _read_multiple_response_sets(metadata: Any) -> list[dict[str, Any]]:
    raw_sets = getattr(metadata, "mr_sets", {}) or {}
    result: list[dict[str, Any]] = []
    for name, definition in raw_sets.items():
        result.append(
            {
                "name": str(name),
                "label": str(definition.get("label") or name),
                "type": str(definition.get("type") or "unknown"),
                "variables": list(definition.get("variable_list") or []),
                "counted_value": _json_scalar(definition.get("counted_value")),
                "source": "spss_metadata",
            }
        )
    return result


def _build_questions(
    frame: pd.DataFrame,
    variables: list[VariableInspection],
    explicit_sets: list[dict[str, Any]],
) -> tuple[list[QuestionInspection], list[str]]:
    by_name = {variable.name: variable for variable in variables}
    grouped: dict[str, tuple[list[str], str, QuestionType]] = {}
    consumed: set[str] = set()
    warnings: list[str] = []

    for response_set in explicit_sets:
        members = [name for name in response_set["variables"] if name in by_name]
        if len(members) >= 2:
            grouped[response_set["name"].lstrip("$")] = (
                members,
                "metadata",
                QuestionType.MULTIPLE_DICHOTOMY,
            )
            consumed.update(members)

    candidates: dict[str, list[str]] = defaultdict(list)
    for variable in variables:
        if variable.name in consumed or variable.role is not VariableRole.QUESTION:
            continue
        prefix = group_prefix(variable.name)
        if prefix:
            candidates[prefix].append(variable.name)

    for prefix, members in candidates.items():
        all_dichotomies = all(
            is_dichotomy(frame[name].dropna().unique()) for name in members
        )
        if len(members) < 2:
            continue
        if all_dichotomies:
            group_type = QuestionType.MULTIPLE_DICHOTOMY
        elif _matrix_compatible(members, by_name):
            group_type = QuestionType.MATRIX
        else:
            continue
        grouped[prefix] = (members, "name_pattern", group_type)
        consumed.update(members)
        group_label = "множественный вопрос" if all_dichotomies else "матрица"
        warnings.append(
            f"Группа {prefix} распознана как {group_label} по именам и структуре; "
            "проверьте её состав."
        )

    questions: list[QuestionInspection] = []
    emitted_groups: set[str] = set()
    group_by_member = {
        member: (code, members, source, group_type)
        for code, (members, source, group_type) in grouped.items()
        for member in members
    }
    for variable in variables:
        group = group_by_member.get(variable.name)
        if group:
            code, members, source, group_type = group
            if code in emitted_groups:
                continue
            emitted_groups.add(code)
            questions.append(
                QuestionInspection(
                    code=code,
                    label=_common_label([by_name[name].label for name in members]) or code,
                    question_type=group_type,
                    role=VariableRole.QUESTION,
                    source_variables=members,
                    valid_count=max(by_name[name].valid_count for name in members),
                    missing_count=min(by_name[name].missing_count for name in members),
                    included_in_report=True,
                    recognition="metadata" if source == "metadata" else "auto_review",
                    warnings=[] if source == "metadata" else ["Автоматически собранная группа."],
                    items=[
                        {"variable": name, "label": by_name[name].label} for name in members
                    ],
                    special_values=_special_values(members, by_name),
                    special_items=[
                        name for name in members if is_special_label(by_name[name].label)
                    ],
                )
            )
            continue

        included = variable.role is VariableRole.QUESTION and variable.question_type not in {
            QuestionType.OPEN_TEXT,
            QuestionType.TECHNICAL,
        }
        questions.append(
            QuestionInspection(
                code=variable.name,
                label=variable.label,
                question_type=variable.question_type,
                role=variable.role,
                source_variables=[variable.name],
                valid_count=variable.valid_count,
                missing_count=variable.missing_count,
                included_in_report=included,
                warnings=list(variable.warnings),
                items=[{"variable": variable.name, "label": variable.label}],
                special_values=[
                    label.value
                    for label in variable.value_labels
                    if is_special_label(label.label)
                ],
            )
        )
    return questions, warnings


def _common_label(labels: list[str]) -> str | None:
    if not labels:
        return None
    words = [label.split() for label in labels]
    common: list[str] = []
    for group in zip(*words, strict=False):
        if len(set(group)) != 1:
            break
        common.append(group[0])
    return " ".join(common).rstrip(" :-–—") or None


def _matrix_compatible(
    members: list[str], variables: dict[str, VariableInspection]
) -> bool:
    inspected = [variables[name] for name in members]
    if not all(variable.storage_type == "numeric" for variable in inspected):
        return False
    signatures = [
        tuple((str(label.value), label.label.casefold()) for label in variable.value_labels)
        for variable in inspected
    ]
    if signatures[0] and all(signature == signatures[0] for signature in signatures[1:]):
        return True
    return all(
        (variable.measurement_level or "").lower() == "scale"
        and 2 <= variable.unique_count <= 12
        for variable in inspected
    )


def _special_values(
    members: list[str], variables: dict[str, VariableInspection]
) -> list[Any]:
    values: list[Any] = []
    for name in members:
        for label in variables[name].value_labels:
            if is_special_label(label.label) and label.value not in values:
                values.append(label.value)
    return values


def _json_scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        return _json_scalar(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
