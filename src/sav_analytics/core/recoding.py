from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .filtering import (
    FilterError,
    conditional_series,
    recoding_columns,
    validate_condition_rules,
    value_options,
)
from .formulas import read_project_frame


class RecodingError(ValueError):
    pass


def validate_recode(
    definition: dict[str, Any],
    variables: list[dict[str, Any]],
    project: dict[str, Any] | None = None,
) -> None:
    if definition.get("mode") == "conditions":
        if project is None:
            raise RecodingError("Логическую переменную можно проверить только в проекте.")
        _validate_conditional_recode(definition, project)
        return
    if definition.get("mode") == "segments":
        if project is None:
            raise RecodingError("Сегментацию можно проверить только в проекте.")
        from .segmentation import SegmentationError, segment_questions

        try:
            segment_questions(definition["variables"], project)
        except SegmentationError as exc:
            raise RecodingError(str(exc)) from exc
        if len(set(definition["variables"])) != len(definition["variables"]):
            raise RecodingError("Переменная сегментации указана дважды.")
        return
    source = next(
        (item for item in variables if item["name"] == definition["source_variable"]), None
    )
    if source is None:
        raise RecodingError("Исходная переменная не найдена в SAV.")
    if definition.get("mode", "ranges") == "categories":
        _validate_categorical_recode(definition, source)
        return
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


def recode_source_values(
    path: str | Path, variable: dict[str, Any], project: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Ответы исходной переменной для раскладки по группам — до сохранения.

    Редактор показывает частоту у каждого ответа и сумму у каждой группы,
    поэтому база новой категории видна, пока её собирают, а не после сохранения.
    """
    name = variable["name"]
    frame = read_project_frame(path, project, [name])
    series = frame[name]
    return {
        "variable": name,
        "label": variable.get("label") or name,
        "total": len(series),
        "missing": int(series.isna().sum()),
        "values": value_options(series, variable),
    }


def calculate_recode_preview(
    path: str | Path, definition: dict[str, Any], project: dict[str, Any] | None = None
) -> dict[str, Any]:
    if definition.get("mode") == "conditions":
        if project is None:
            raise RecodingError("Логическую переменную можно посчитать только в проекте.")
        return _conditional_preview(path, definition, project)
    if definition.get("mode") == "segments":
        if project is None:
            raise RecodingError("Сегментацию можно посчитать только в проекте.")
        return _segment_preview(path, definition, project)
    source_variable = definition["source_variable"]
    frame = read_project_frame(path, project, [source_variable])
    series = pd.to_numeric(frame[source_variable], errors="coerce")
    if definition.get("mode", "ranges") == "categories":
        return _categorical_preview(frame[source_variable], definition)
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
        "mode": "ranges",
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


def _validate_categorical_recode(
    definition: dict[str, Any], source: dict[str, Any]
) -> None:
    categories = definition["categories"]
    if len(categories) < 2:
        raise RecodingError("Нужно задать минимум две новые категории.")
    labels = [category["label"].strip().casefold() for category in categories]
    if len(labels) != len(set(labels)):
        raise RecodingError("Названия новых категорий не должны повторяться.")
    assigned: list[Any] = []
    for category in categories:
        values = category.get("values") or []
        if not values:
            raise RecodingError(f"Категория «{category['label']}» не содержит значений.")
        for value in values:
            if _contains_value(assigned, value):
                raise RecodingError("Одно исходное значение нельзя включить в две категории.")
            assigned.append(value)
    available = [item["value"] for item in source.get("value_labels", [])]
    if available:
        unknown = [value for value in assigned if not _contains_value(available, value)]
        if unknown:
            raise RecodingError("В перекодировке найдены значения, отсутствующие в SAV.")


def _categorical_preview(series: pd.Series, definition: dict[str, Any]) -> dict[str, Any]:
    valid = series.dropna()
    assigned = pd.Series(False, index=series.index)
    rows = []
    for position, category in enumerate(definition["categories"], start=1):
        category_values = tuple(category["values"])
        mask = series.map(
            lambda item, values=category_values: any(
                _values_equal(item, value) for value in values
            )
        )
        mask &= series.notna()
        assigned |= mask
        count = int(mask.sum())
        rows.append(
            {
                "value": position,
                "label": category["label"],
                "source_values": category["values"],
                "count": count,
                "percent_total": count / len(series) if len(series) else None,
            }
        )
    return {
        "id": definition.get("id"),
        "code": definition["code"],
        "name": definition["name"],
        "source_variable": definition["source_variable"],
        "mode": "categories",
        "total_base": len(series),
        "source_valid_base": len(valid),
        "source_missing_count": int(series.isna().sum()),
        "out_of_range_count": int((series.notna() & ~assigned).sum()),
        "rows": rows,
    }


def _contains_value(values: list[Any], expected: Any) -> bool:
    return any(_values_equal(value, expected) for value in values)


def _values_equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right) or str(_scalar(left)) == str(_scalar(right))
    except (TypeError, ValueError):
        return False


def _scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def _validate_conditional_recode(definition: dict[str, Any], project: dict[str, Any]) -> None:
    categories = definition["categories"]
    if len(categories) < 2:
        raise RecodingError("Нужно задать минимум две категории.")
    labels = [category["label"].strip().casefold() for category in categories]
    if len(labels) != len(set(labels)):
        raise RecodingError("Названия категорий не должны повторяться.")
    try:
        validate_condition_rules(definition, project)
    except FilterError as exc:
        raise RecodingError(str(exc)) from exc


def _conditional_preview(
    path: str | Path, definition: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    try:
        columns = sorted(recoding_columns(definition, project))
    except FilterError as exc:
        raise RecodingError(str(exc)) from exc
    frame = read_project_frame(path, project, columns)
    try:
        series = conditional_series(definition, project, frame)
    except FilterError as exc:
        raise RecodingError(str(exc)) from exc
    total = len(frame)
    rows = []
    for position, category in enumerate(definition["categories"], start=1):
        label = category["label"]
        count = int(series.map(lambda item, expected=label: item == expected).sum())
        rows.append(
            {
                "value": position,
                "label": label,
                "count": count,
                "percent_total": count / total if total else None,
            }
        )
    unassigned = int(series.isna().sum())
    return {
        "id": definition.get("id"),
        "code": definition["code"],
        "name": definition["name"],
        "source_variable": None,
        "mode": "conditions",
        "total_base": total,
        "source_valid_base": total - unassigned,
        "source_missing_count": 0,
        "out_of_range_count": unassigned,
        "rows": rows,
    }


def suggest_ranges(
    path: str | Path,
    variable: dict[str, Any],
    method: str,
    groups: int,
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Диапазоны числовой переменной: равные по численности группы или интервалы.

    Границы диапазона включаются с обеих сторон, а пересекаться диапазоны не
    могут, поэтому следующая группа начинается на шаг сетки выше предыдущей.
    Шаг — точность самих данных (до трёх знаков): значение с такой точностью
    не может попасть между группами. Совпадающие границы квантилей
    схлопываются, и групп становится меньше — это видно в ответе.
    """
    if variable.get("storage_type") != "numeric":
        raise RecodingError("Разбить на диапазоны можно только числовую переменную.")
    if method not in {"quantiles", "equal"}:
        raise RecodingError("Неизвестный способ разбиения.")
    if not 2 <= groups <= 20:
        raise RecodingError("Групп должно быть от 2 до 20.")
    name = variable["name"]
    frame = read_project_frame(path, project, [name])
    values = pd.to_numeric(frame[name], errors="coerce").dropna()
    if values.nunique() < 2:
        raise RecodingError("У переменной меньше двух различных значений.")
    decimals = _decimals(values)
    step = 10.0 ** -decimals
    low, high = float(values.min()), float(values.max())
    if method == "quantiles":
        raw_cuts = [float(values.quantile(index / groups)) for index in range(1, groups)]
    else:
        width = (high - low) / groups
        raw_cuts = [low + width * index for index in range(1, groups)]
    cuts: list[float] = []
    for cut in raw_cuts:
        # Верхняя граница группы — на сетке данных, не выше квантиля.
        snapped = round(np.floor(cut / step + 1e-9) * step, decimals)
        if low <= snapped < high and (not cuts or snapped > cuts[-1]):
            cuts.append(snapped)
    if not cuts:
        raise RecodingError("Значения слишком сосредоточены: разбить на группы не удалось.")
    categories = []
    for index in range(len(cuts) + 1):
        lower = None if index == 0 else round(cuts[index - 1] + step, decimals)
        upper = None if index == len(cuts) else cuts[index]
        mask = pd.Series(True, index=values.index)
        if lower is not None:
            mask &= values >= lower
        if upper is not None:
            mask &= values <= upper
        categories.append(
            {
                "label": _range_label(lower, upper, decimals),
                "lower": lower,
                "upper": upper,
                "count": int(mask.sum()),
            }
        )
    return {
        "variable": name,
        "method": method,
        "requested": groups,
        "decimals": decimals,
        "valid": int(values.size),
        "categories": categories,
    }


def _decimals(values: pd.Series) -> int:
    for decimals in range(4):
        scaled = values * 10**decimals
        if bool(np.all(np.abs(scaled - np.round(scaled)) < 1e-6)):
            return decimals
    return 3


def _range_label(lower: float | None, upper: float | None, decimals: int) -> str:
    def number(value: float) -> str:
        return f"{value:.{decimals}f}".replace(".", ",")

    if lower is None:
        return f"до {number(upper)}"
    if upper is None:
        return f"{number(lower)} и больше"
    return f"{number(lower)}–{number(upper)}"



def _segment_preview(
    path: str | Path, definition: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    """Размер каждого сегмента на текущих данных и профиль его центра."""
    from .segmentation import SegmentationError, segment_columns, segment_series

    try:
        frame = read_project_frame(path, project, segment_columns(definition, project))
        labels = segment_series(definition, project, frame)
    except SegmentationError as exc:
        raise RecodingError(str(exc)) from exc
    profiles = (definition.get("model") or {}).get("profiles") or []
    rows = []
    for position, category in enumerate(definition["categories"], start=1):
        count = int((labels == category["label"]).sum())
        profile = next((item for item in profiles if item["position"] == position), None)
        rows.append(
            {
                "value": position,
                "label": category["label"],
                "count": count,
                "percent_total": count / len(frame) if len(frame) else None,
                "means": (profile or {}).get("means", {}),
            }
        )
    return {
        "id": definition.get("id"),
        "code": definition["code"],
        "name": definition["name"],
        "mode": "segments",
        "total_base": len(frame),
        "missing_count": int(labels.isna().sum()),
        "variables": definition["variables"],
        "rows": rows,
    }
