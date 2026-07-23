from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat


class RecodingError(ValueError):
    pass


def validate_numeric_recode(
    definition: dict[str, Any], variables: list[dict[str, Any]]
) -> None:
    source = next(
        (item for item in variables if item["name"] == definition["source_variable"]), None
    )
    if source is None:
        raise RecodingError("Исходная переменная не найдена в SAV.")
    if source["storage_type"] != "numeric":
        raise RecodingError("Диапазоны можно создать только для числовой переменной.")

    categories = definition["categories"]
    if len(categories) < 2:
        raise RecodingError("Нужно задать минимум две категории.")
    labels = [category["label"].strip().casefold() for category in categories]
    if len(labels) != len(set(labels)):
        raise RecodingError("Названия категорий не должны повторяться.")

    for category in categories:
        lower = category.get("lower")
        upper = category.get("upper")
        if lower is None and upper is None:
            raise RecodingError("Категория должна иметь хотя бы одну границу.")
        if lower is not None and upper is not None and lower > upper:
            raise RecodingError("Нижняя граница категории не может быть выше верхней.")

    for index, left in enumerate(categories):
        for right in categories[index + 1 :]:
            if _ranges_overlap(left, right):
                raise RecodingError(
                    f"Диапазоны «{left['label']}» и «{right['label']}» пересекаются."
                )


def calculate_recode_preview(path: str | Path, definition: dict[str, Any]) -> dict[str, Any]:
    source_variable = definition["source_variable"]
    frame, _ = pyreadstat.read_sav(
        path,
        usecols=[source_variable],
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    series = pd.to_numeric(frame[source_variable], errors="coerce")
    assigned = pd.Series(False, index=series.index)
    rows = []
    for position, category in enumerate(definition["categories"], start=1):
        mask = series.notna()
        if category.get("lower") is not None:
            mask &= series >= category["lower"]
        if category.get("upper") is not None:
            mask &= series <= category["upper"]
        count = int(mask.sum())
        assigned |= mask
        rows.append(
            {
                "value": position,
                "label": category["label"],
                "lower": category.get("lower"),
                "upper": category.get("upper"),
                "count": count,
                "percent_total": count / len(series) if len(series) else None,
            }
        )
    valid = series.notna()
    return {
        "id": definition.get("id"),
        "code": definition["code"],
        "name": definition["name"],
        "source_variable": source_variable,
        "total_base": len(series),
        "source_valid_base": int(valid.sum()),
        "source_missing_count": int(series.isna().sum()),
        "out_of_range_count": int((valid & ~assigned).sum()),
        "rows": rows,
    }


def _ranges_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_lower = float("-inf") if left.get("lower") is None else left["lower"]
    left_upper = float("inf") if left.get("upper") is None else left["upper"]
    right_lower = float("-inf") if right.get("lower") is None else right["lower"]
    right_upper = float("inf") if right.get("upper") is None else right["upper"]
    return max(left_lower, right_lower) <= min(left_upper, right_upper)

