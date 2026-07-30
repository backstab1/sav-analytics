from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat
import xlsxwriter

from .statistics import effective_sample_size


class WeightingError(ValueError):
    pass


@dataclass(frozen=True)
class RakingResult:
    weights: pd.Series
    iterations: int
    maximum_deviation: float
    diagnostics: dict[str, Any]


def build_raking_export(
    path: str | Path,
    definition: dict[str, Any],
    project: dict[str, Any],
) -> bytes:
    """Build a respondent-level XLSX with identifiers and calculated raking weights."""
    questions = project["configuration"]["questions"]
    id_question = next((item for item in questions if item.get("role") == "id"), None)
    weight_question = next((item for item in questions if item.get("role") == "weight"), None)
    extra_variables = []
    for question in (id_question, weight_question):
        if question and len(question.get("source_variables", [])) == 1:
            extra_variables.append(question["source_variables"][0])
    dimension_variables = [item["variable"] for item in definition["dimensions"]]
    variables = list(dict.fromkeys([*dimension_variables, *extra_variables]))
    frame, _ = pyreadstat.read_sav(
        path,
        usecols=variables,
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    result = calculate_raking(frame, definition)

    identifier_name = (
        id_question["source_variables"][0]
        if id_question and len(id_question.get("source_variables", [])) == 1
        else None
    )
    source_weight_name = (
        weight_question["source_variables"][0]
        if weight_question and len(weight_question.get("source_variables", [])) == 1
        else None
    )
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties(
        {
            "title": f"Рассчитанный вес — {definition['name']}",
            "subject": "Респондентский экспорт веса raking/IPF",
            "author": "sav-analytics",
        }
    )
    sheet = workbook.add_worksheet("Вес")
    sheet.hide_gridlines(2)
    sheet.freeze_panes(1, 0)
    header = workbook.add_format(
        {
            "font_name": "Arial",
            "font_size": 10,
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#355B47",
            "align": "center",
            "valign": "vcenter",
            "bottom": 1,
            "bottom_color": "#244532",
        }
    )
    text_format = workbook.add_format({"font_name": "Arial", "font_size": 9})
    integer_format = workbook.add_format(
        {"font_name": "Arial", "font_size": 9, "num_format": "0"}
    )
    weight_format = workbook.add_format(
        {"font_name": "Arial", "font_size": 9, "num_format": "0.000000"}
    )
    headers = [id_question["label"] if identifier_name else "Номер строки"]
    if source_weight_name:
        headers.append(f"Исходный вес ({source_weight_name})")
    headers.append("Рассчитанный вес")
    sheet.write_row(0, 0, headers, header)
    for row_index, frame_index in enumerate(frame.index, start=1):
        identifier = frame.at[frame_index, identifier_name] if identifier_name else row_index
        _write_export_value(sheet, row_index, 0, identifier, text_format, integer_format)
        column = 1
        if source_weight_name:
            _write_export_value(
                sheet,
                row_index,
                column,
                frame.at[frame_index, source_weight_name],
                text_format,
                weight_format,
            )
            column += 1
        sheet.write_number(row_index, column, float(result.weights.loc[frame_index]), weight_format)
    last_row = len(frame)
    last_column = len(headers) - 1
    sheet.autofilter(0, 0, last_row, last_column)
    sheet.set_column(0, 0, 22)
    if last_column >= 1:
        sheet.set_column(1, last_column, 20)
    sheet.set_row(0, 24)
    workbook.close()
    return output.getvalue()


def _write_export_value(
    sheet: Any,
    row: int,
    column: int,
    value: Any,
    text_format: Any,
    numeric_format: Any,
) -> None:
    if pd.isna(value):
        sheet.write_blank(row, column, None, text_format)
    elif isinstance(value, str):
        sheet.write_string(row, column, value, text_format)
    elif isinstance(value, (int, float)) or hasattr(value, "item"):
        numeric = value.item() if hasattr(value, "item") else value
        sheet.write_number(row, column, float(numeric), numeric_format)
    else:
        sheet.write_string(row, column, str(value), text_format)


def calculate_raking_preview(
    path: str | Path, definition: dict[str, Any]
) -> dict[str, Any]:
    variables = list(dict.fromkeys(item["variable"] for item in definition["dimensions"]))
    frame, _ = pyreadstat.read_sav(
        path,
        usecols=variables,
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    result = calculate_raking(frame, definition)
    return {
        "id": definition.get("id"),
        "name": definition["name"],
        **result.diagnostics,
    }


def calculate_raking(
    frame: pd.DataFrame,
    definition: dict[str, Any],
) -> RakingResult:
    dimensions = definition.get("dimensions", [])
    if not dimensions:
        raise WeightingError("Добавьте хотя бы одно целевое распределение.")
    tolerance = float(definition.get("tolerance", 0.001))
    maximum_iterations = int(definition.get("maximum_iterations", 500))
    if not 0 < tolerance < 1:
        raise WeightingError("Допуск сходимости должен находиться между 0 и 1.")
    if maximum_iterations < 1:
        raise WeightingError("Число итераций должно быть положительным.")
    lower = definition.get("lower_bound", 0.3)
    upper = definition.get("upper_bound", 3.0)
    if lower is not None:
        lower = float(lower)
    if upper is not None:
        upper = float(upper)
    if lower is not None and lower <= 0:
        raise WeightingError("Нижняя граница веса должна быть положительной.")
    if upper is not None and upper <= 0:
        raise WeightingError("Верхняя граница веса должна быть положительной.")
    if lower is not None and upper is not None and lower >= upper:
        raise WeightingError("Нижняя граница веса должна быть меньше верхней.")

    prepared = [_prepare_dimension(frame, dimension) for dimension in dimensions]
    weights = pd.Series(1.0, index=frame.index)
    maximum_deviation = float("inf")
    for iteration in range(1, maximum_iterations + 1):
        for dimension in prepared:
            total_weight = float(weights.sum())
            for category in dimension["categories"]:
                current = float(weights[category["mask"]].sum())
                if current <= 0:
                    raise WeightingError(
                        f"В измерении «{dimension['label']}» отсутствует категория "
                        f"«{category['label']}» с ненулевой базой."
                    )
                desired = total_weight * category["target_share"]
                weights.loc[category["mask"]] *= desired / current
        if lower is not None or upper is not None:
            weights = weights.clip(lower=lower, upper=upper)
        weights /= float(weights.mean())
        maximum_deviation = _maximum_deviation(weights, prepared)
        if maximum_deviation < tolerance:
            return RakingResult(
                weights=weights,
                iterations=iteration,
                maximum_deviation=maximum_deviation,
                diagnostics=_diagnostics(weights, prepared, iteration, maximum_deviation),
            )
    raise WeightingError(
        "Raking не сошёлся за "
        f"{maximum_iterations} итераций; максимальное отклонение "
        f"{maximum_deviation * 100:.3f} п.п."
    )


def _prepare_dimension(frame: pd.DataFrame, definition: dict[str, Any]) -> dict[str, Any]:
    variable = definition.get("variable")
    if not variable or variable not in frame.columns:
        raise WeightingError("Переменная взвешивания не найдена в SAV.")
    targets = definition.get("targets", [])
    if len(targets) < 2:
        raise WeightingError("Целевое распределение должно содержать минимум две категории.")
    total_percent = sum(float(target["percent"]) for target in targets)
    if not 99.9 <= total_percent <= 100.1:
        raise WeightingError("Целевые доли каждого распределения должны давать 100%.")
    series = frame[variable]
    if series.isna().any():
        raise WeightingError(
            f"Переменная «{definition.get('label') or variable}» содержит пропуски."
        )
    coverage = pd.Series(0, index=frame.index, dtype=int)
    categories = []
    for target in targets:
        values = target.get("values", [])
        if not values:
            raise WeightingError("Для каждой целевой категории укажите исходные значения.")
        mask = series.map(
            lambda value, expected=values: any(_equal(value, item) for item in expected)
        )
        if not mask.any():
            raise WeightingError(
                f"В массиве отсутствует целевая категория «{target['label']}»."
            )
        coverage += mask.astype(int)
        categories.append(
            {
                "label": target["label"],
                "mask": mask,
                "target_share": float(target["percent"]) / total_percent,
            }
        )
    if (coverage == 0).any():
        raise WeightingError(
            f"Распределение «{definition.get('label') or variable}» не покрывает все строки."
        )
    if (coverage > 1).any():
        raise WeightingError(
            f"Категории распределения «{definition.get('label') or variable}» пересекаются."
        )
    return {
        "variable": variable,
        "label": definition.get("label") or variable,
        "categories": categories,
    }


def _maximum_deviation(weights: pd.Series, dimensions: list[dict[str, Any]]) -> float:
    total = float(weights.sum())
    return max(
        abs(float(weights[category["mask"]].sum()) / total - category["target_share"])
        for dimension in dimensions
        for category in dimension["categories"]
    )


def _diagnostics(
    weights: pd.Series,
    dimensions: list[dict[str, Any]],
    iterations: int,
    maximum_deviation: float,
) -> dict[str, Any]:
    effective_base = effective_sample_size(weights)
    distributions = []
    for dimension in dimensions:
        categories = []
        for category in dimension["categories"]:
            mask = category["mask"]
            categories.append(
                {
                    "label": category["label"],
                    "target_percent": category["target_share"] * 100,
                    "before_percent": float(mask.mean()) * 100,
                    "after_percent": float(weights[mask].sum() / weights.sum()) * 100,
                    "base": int(mask.sum()),
                }
            )
        distributions.append(
            {
                "variable": dimension["variable"],
                "label": dimension["label"],
                "categories": categories,
            }
        )
    return {
        "iterations": iterations,
        "maximum_deviation_pp": maximum_deviation * 100,
        "minimum": float(weights.min()),
        "maximum": float(weights.max()),
        "mean": float(weights.mean()),
        "stddev": float(weights.std(ddof=1)) if len(weights) > 1 else 0.0,
        "effective_base": effective_base,
        "design_effect": len(weights) / effective_base,
        "efficiency_percent": effective_base / len(weights) * 100,
        "distributions": distributions,
    }


def _equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right) or str(left) == str(right)
    except (TypeError, ValueError):
        return False
