from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat

from .models import QuestionType


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
        return {
            **base,
            **_multiple_preview(frame, source_variables, variable_by_name),
        }
    if len(source_variables) != 1:
        raise ToplineError("Для этого типа вопроса ожидается одна исходная переменная.")
    series = frame[source_variables[0]]
    if question_type in {QuestionType.SINGLE_CHOICE, QuestionType.SCALE}:
        result = _categorical_preview(series, variable_by_name[source_variables[0]], len(frame))
        if question_type is QuestionType.SCALE:
            result["statistics"] = _numeric_statistics(series)
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
    series: pd.Series, variable: dict[str, Any], total_base: int
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
            }
        )
    return {"valid_base": valid_base, "rows": rows, "statistics": None}


def _multiple_preview(
    frame: pd.DataFrame,
    source_variables: list[str],
    variable_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    answered = frame[source_variables].notna().any(axis=1)
    valid_base = int(answered.sum())
    rows = []
    for name in source_variables:
        series = frame[name]
        count = int(series.eq(1).sum())
        variable = variable_by_name[name]
        rows.append(
            {
                "value": name,
                "label": variable["label"],
                "count": count,
                "percent_main": _ratio(count, len(frame)),
                "percent_filter": _ratio(count, valid_base),
            }
        )
    return {
        "valid_base": valid_base,
        "rows": rows,
        "statistics": None,
        "warnings": ["Для автоматически найденной дихотомии выбранным считается код 1."],
    }


def _numeric_statistics(series: pd.Series) -> dict[str, float | int | None]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
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

