from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat

from .models import QuestionType
from .multiple_response import (
    MultipleResponseError,
    answered_mask,
    response_definition,
    selected_mask,
)


class ToplineError(ValueError):
    pass


def calculate_preview(
    path: str | Path,
    question: dict[str, Any],
    variables: list[dict[str, Any]],
) -> dict[str, Any]:
    source_variables = question["source_variables"]
    frame, _ = pyreadstat.read_sav(
        path,
        usecols=source_variables,
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    question_type = QuestionType(question["question_type"])
    base = {
        "code": question["code"],
        "label": question["label"],
        "question_type": question_type,
        "total_base": len(frame),
    }
    variable_by_name = {item["name"]: item for item in variables}

    if question_type is QuestionType.MULTIPLE_DICHOTOMY:
        try:
            return {
                **base,
                **_multiple_preview(
                    frame,
                    question,
                    variable_by_name,
                    question.get("special_items", []),
                ),
            }
        except MultipleResponseError as exc:
            raise ToplineError(str(exc)) from exc
    if question_type is QuestionType.MATRIX:
        return {
            **base,
            **_matrix_preview(
                frame,
                source_variables,
                variable_by_name,
                question.get("special_values", []),
            ),
        }
    if question_type in {
        QuestionType.MULTIPLE_CATEGORICAL,
        QuestionType.RANKING,
    }:
        raise ToplineError("Этот тип вопроса пока не поддерживается в расчётах.")
    if len(source_variables) != 1:
        raise ToplineError("Для этого типа вопроса ожидается одна исходная переменная.")
    series = frame[source_variables[0]]
    if question_type in {QuestionType.SINGLE_CHOICE, QuestionType.SCALE}:
        special_values = question.get("special_values", [])
        result = _categorical_preview(
            series,
            variable_by_name[source_variables[0]],
            len(frame),
            special_values,
            question.get("not_applicable_values", []),
        )
        if question_type is QuestionType.SCALE:
            result["statistics"] = _numeric_statistics(series, special_values)
        return {**base, **result}
    if question_type is QuestionType.NUMERIC:
        return {
            **base,
            "valid_base": int(series.notna().sum()),
            "rows": [],
            "statistics": _numeric_statistics(series),
        }
    raise ToplineError("Для исключённого или технического вопроса предпросмотр не строится.")


def _categorical_preview(
    series: pd.Series,
    variable: dict[str, Any],
    total_base: int,
    special_values: list[Any] | None = None,
    not_applicable: list[Any] | None = None,
) -> dict[str, Any]:
    valid = series.dropna()
    valid_base = len(valid)
    labels = {str(item["value"]): item["label"] for item in variable["value_labels"]}
    counts = valid.value_counts(sort=False)
    ordered_values: list[Any] = [item["value"] for item in variable["value_labels"]]
    ordered_values.extend(
        value for value in counts.index if not _contains_value(ordered_values, value)
    )
    rows = []
    for value in ordered_values:
        count = int(_count_value(valid, value))
        rows.append(
            {
                "value": _scalar(value),
                "label": labels.get(str(_scalar(value)), str(_scalar(value))),
                "count": count,
                "percent_main": _ratio(count, total_base),
                "percent_filter": _ratio(count, valid_base),
                "is_special": _contains_value(special_values or [], value),
                "is_not_applicable": _contains_value(not_applicable or [], value),
            }
        )
    return {"valid_base": valid_base, "rows": rows, "statistics": None}


def _multiple_preview(
    frame: pd.DataFrame,
    question: dict[str, Any],
    variable_by_name: dict[str, dict[str, Any]],
    special_items: list[str],
) -> dict[str, Any]:
    source_variables = question["source_variables"]
    answered = answered_mask(frame, question)
    valid_base = int(answered.sum())
    rows = []
    for name in source_variables:
        count = int(selected_mask(frame, question, name).sum())
        variable = variable_by_name[name]
        rows.append(
            {
                "value": name,
                "label": variable["label"],
                "count": count,
                "percent_main": _ratio(count, len(frame)),
                "percent_filter": _ratio(count, valid_base),
                "is_special": name in special_items,
            }
        )
    counted_value = response_definition(question).get("counted_value")
    warnings = [f"Выбранным считается код {counted_value}."]
    special = [name for name in special_items if name in source_variables]
    ordinary = [name for name in source_variables if name not in special]
    if special and ordinary:
        conflicting = pd.concat(
            [selected_mask(frame, question, name) for name in special], axis=1
        ).any(axis=1) & pd.concat(
            [selected_mask(frame, question, name) for name in ordinary], axis=1
        ).any(axis=1)
        conflict_count = int(conflicting.sum())
        if conflict_count:
            warnings.append(
                f"У {conflict_count} респондентов спецответ выбран вместе с обычным вариантом."
            )
    return {
        "valid_base": valid_base,
        "rows": rows,
        "statistics": None,
        "warnings": warnings,
    }


def _matrix_preview(
    frame: pd.DataFrame,
    source_variables: list[str],
    variable_by_name: dict[str, dict[str, Any]],
    special_values: list[Any],
) -> dict[str, Any]:
    items = []
    for name in source_variables:
        series = frame[name]
        distribution = _categorical_preview(
            series, variable_by_name[name], len(frame), special_values
        )
        items.append(
            {
                "variable": name,
                "label": variable_by_name[name]["label"],
                "valid_base": distribution["valid_base"],
                "rows": distribution["rows"],
                "statistics": _numeric_statistics(series, special_values),
            }
        )
    return {
        "valid_base": int(frame[source_variables].notna().any(axis=1).sum()),
        "rows": [],
        "statistics": None,
        "items": items,
    }


def _numeric_statistics(
    series: pd.Series, special_values: list[Any] | None = None
) -> dict[str, float | int | None]:
    working = series.copy()
    for value in special_values or []:
        working = working.mask(
            working.map(lambda item, expected=value: _values_equal(item, expected))
        )
    numeric = pd.to_numeric(working, errors="coerce").dropna()
    count = len(numeric)
    if not count:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "minimum": None,
            "maximum": None,
            "stddev": None,
            "stderr": None,
        }
    stddev = float(numeric.std(ddof=1)) if count > 1 else None
    return {
        "count": count,
        "mean": float(numeric.mean()),
        "median": float(numeric.median()),
        "minimum": float(numeric.min()),
        "maximum": float(numeric.max()),
        "stddev": stddev,
        "stderr": stddev / math.sqrt(count) if stddev is not None else None,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _contains_value(values: list[Any], candidate: Any) -> bool:
    return any(_values_equal(value, candidate) for value in values)


def _count_value(series: pd.Series, expected: Any) -> int:
    return int(sum(_values_equal(value, expected) for value in series))


def _values_equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right) or str(_scalar(left)) == str(_scalar(right))
    except (TypeError, ValueError):
        return False


def _scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value
