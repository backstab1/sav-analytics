from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd
import xlsxwriter

from .banner import BannerError, _source_categories, _source_columns
from .formulas import read_project_frame
from .statistics import effective_sample_size


class WeightingError(ValueError):
    pass


@dataclass(frozen=True)
class RakingResult:
    weights: pd.Series
    iterations: int
    maximum_deviation: float
    diagnostics: dict[str, Any]


def calculate_weight(
    frame: pd.DataFrame,
    definition: dict[str, Any],
    project: dict[str, Any] | None = None,
) -> RakingResult:
    """Рассчитанный вес проекта тем методом, который в нём выбран.

    Если в проекте есть переменная с ролью «Волна» и в массиве больше одной
    волны, вес считается отдельно внутри каждой волны с теми же целями и
    нормируется к среднему 1 внутри волны (`requirements.md` §10). Иначе
    волна с другим составом выборки перетягивала бы цели соседней, и
    сравнение волн мерило бы разницу весов, а не мнений.
    """
    frame, definition = _with_recoding_dimensions(frame, definition, project)
    wave = project_wave_variable(project) if project else None
    if wave is None:
        return _calculate_single(frame, definition)
    if wave not in frame.columns:
        raise WeightingError("Переменная волны не прочитана из SAV.")
    series = frame[wave]
    if series.isna().any():
        raise WeightingError(
            f"У {int(series.isna().sum())} респондентов не указана волна: "
            "вес считается внутри каждой волны."
        )
    values = list(dict.fromkeys(series.tolist()))
    if len(values) < 2:
        return _calculate_single(frame, definition)
    labels = _value_labels(project, wave)
    weights = pd.Series(0.0, index=frame.index)
    waves = []
    iterations = 0
    deviation = 0.0
    for value in values:
        mask = series.map(lambda item, expected=value: _equal(item, expected))
        label = labels.get(str(_scalar(value)), str(_scalar(value)))
        try:
            part = _calculate_single(frame[mask], definition)
        except WeightingError as exc:
            raise WeightingError(f"Волна «{label}»: {exc}") from exc
        weights.loc[mask] = part.weights
        iterations = max(iterations, part.iterations)
        deviation = max(deviation, part.maximum_deviation)
        waves.append(
            {
                "label": label,
                "base": int(mask.sum()),
                "iterations": part.iterations,
                "effective_base": part.diagnostics["effective_base"],
                "design_effect": part.diagnostics["design_effect"],
            }
        )
    cells = definition.get("method", "raking") == "cells"
    prepared = [
        _prepare_dimension(frame, dimension, shares=not cells)
        for dimension in definition.get("dimensions", [])
    ]
    if cells:
        total = float(weights.sum())
        for dimension in prepared:
            for category in dimension["categories"]:
                category["target_share"] = float(weights[category["mask"]].sum()) / total
    diagnostics = _diagnostics(weights, prepared, iterations, deviation)
    diagnostics["waves"] = waves
    return RakingResult(
        weights=weights, iterations=iterations, maximum_deviation=deviation, diagnostics=diagnostics
    )


def weight_columns(definition: dict[str, Any], project: dict[str, Any]) -> list[str]:
    """Столбцы SAV, нужные для расчёта веса: измерения, их перекодировки и волна."""
    columns: list[str] = []
    for dimension in definition.get("dimensions", []):
        if dimension.get("recoding_id"):
            source = {"kind": "recoding", "ref": dimension["recoding_id"]}
            try:
                columns.extend(_source_columns(source, project))
            except BannerError as exc:
                raise WeightingError(str(exc)) from exc
        else:
            columns.append(dimension["variable"])
    wave = project_wave_variable(project)
    if wave:
        columns.append(wave)
    return list(dict.fromkeys(columns))


def _with_recoding_dimensions(
    frame: pd.DataFrame, definition: dict[str, Any], project: dict[str, Any] | None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Измерение по сохранённой перекодировке — служебным столбцом её категорий.

    Категории перекодировки берутся тем же `_source_categories`, что у колонок
    баннера, поэтому группа «18–34» в весе и в баннере — один и тот же набор
    респондентов. Цель категории сопоставляется по её номеру в перекодировке.
    """
    dimensions = definition.get("dimensions", [])
    if not any(dimension.get("recoding_id") for dimension in dimensions):
        return frame, definition
    if project is None:
        raise WeightingError("Измерение по перекодировке считается только в проекте.")
    frame = frame.copy()
    prepared = []
    for dimension in dimensions:
        recoding_id = dimension.get("recoding_id")
        if not recoding_id:
            prepared.append(dimension)
            continue
        try:
            resolved = _source_categories({"kind": "recoding", "ref": recoding_id}, project, frame)
        except BannerError as exc:
            raise WeightingError(str(exc)) from exc
        series = pd.Series(float("nan"), index=frame.index)
        for category in resolved["categories"]:
            series = series.where(series.notna() | ~category["mask"], float(category["value"]))
        column = f"__weight_recoding_{recoding_id}"
        frame[column] = series
        prepared.append({**dimension, "variable": column})
    return frame, {**definition, "dimensions": prepared}


def _calculate_single(frame: pd.DataFrame, definition: dict[str, Any]) -> RakingResult:
    if definition.get("method", "raking") == "cells":
        return calculate_cell_weighting(frame, definition)
    return calculate_raking(frame, definition)


def project_wave_variable(project: dict[str, Any]) -> str | None:
    """Переменная SAV вопроса с ролью «Волна», если он есть."""
    for question in project.get("configuration", {}).get("questions", []):
        if question.get("role") == "wave" and len(question.get("source_variables", [])) == 1:
            return str(question["source_variables"][0])
    return None


def _value_labels(project: dict[str, Any], variable: str) -> dict[str, str]:
    found = next(
        (
            item
            for item in project.get("inspection", {}).get("variables", [])
            if item["name"] == variable
        ),
        None,
    )
    return {
        str(_scalar(item["value"])): item["label"] for item in (found or {}).get("value_labels", [])
    }


def _scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def weight_method_label(definition: dict[str, Any]) -> str:
    return "по ячейкам" if definition.get("method", "raking") == "cells" else "raking/IPF"


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
    variables = list(dict.fromkeys([*weight_columns(definition, project), *extra_variables]))
    frame = read_project_frame(path, project, variables)
    result = calculate_weight(frame, definition, project)

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
            "subject": f"Респондентский экспорт веса {weight_method_label(definition)}",
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
    path: str | Path, definition: dict[str, Any], project: dict[str, Any] | None = None
) -> dict[str, Any]:
    if project is None:
        variables = list(dict.fromkeys(item["variable"] for item in definition["dimensions"]))
    else:
        variables = weight_columns(definition, project)
    frame = read_project_frame(path, project, variables)
    result = calculate_weight(frame, definition, project)
    return {
        "id": definition.get("id"),
        "name": definition["name"],
        "method": definition.get("method", "raking"),
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


def calculate_cell_weighting(
    frame: pd.DataFrame,
    definition: dict[str, Any],
) -> RakingResult:
    """Взвешивание по ячейкам сочетаний: вес ячейки — цель, делённая на долю в выборке.

    В отличие от raking, цели задаются совместному распределению, поэтому
    результат точный и итераций не требует. Ограничения веса здесь не
    обрезают, а проверяют: обрезка ячейкового веса молча увела бы ячейку от
    цели. Ячейка с весом вне границ, пустая ячейка с ненулевой целью и
    респонденты в ячейке с нулевой целью — ошибки с именем ячейки, лечатся
    объединением категорий или правкой целей.
    """
    dimensions = definition.get("dimensions", [])
    if not dimensions:
        raise WeightingError("Добавьте хотя бы одну переменную ячеек.")
    if len(dimensions) > 3:
        raise WeightingError("Ячейки строятся не более чем по трём переменным.")
    prepared = [_prepare_dimension(frame, dimension, shares=False) for dimension in dimensions]
    for dimension in prepared:
        labels = [category["label"] for category in dimension["categories"]]
        if len(set(labels)) != len(labels):
            raise WeightingError(f"В «{dimension['label']}» повторяются подписи категорий.")
    targets: dict[tuple[str, ...], float] = {}
    for cell in definition.get("cells", []):
        key = tuple(str(label) for label in cell.get("categories", []))
        if len(key) != len(prepared):
            raise WeightingError("Каждая ячейка должна называть категорию каждой переменной.")
        if key in targets:
            raise WeightingError(f"Ячейка «{' × '.join(key)}» задана дважды.")
        targets[key] = float(cell.get("percent") or 0)
    total_percent = sum(targets.values())
    if not 99.9 <= total_percent <= 100.1:
        raise WeightingError(f"Цели ячеек должны давать 100%. Сейчас {total_percent:.2f}%.")
    lower = definition.get("lower_bound")
    upper = definition.get("upper_bound")

    size = len(frame)
    weights = pd.Series(0.0, index=frame.index)
    cells = []
    for combination in product(*(dimension["categories"] for dimension in prepared)):
        key = tuple(category["label"] for category in combination)
        label = " × ".join(key)
        mask = pd.Series(True, index=frame.index)
        for category in combination:
            mask &= category["mask"]
        base = int(mask.sum())
        share = targets.pop(key, 0.0) / total_percent
        if base == 0 and share > 0:
            raise WeightingError(
                f"Ячейка «{label}» пуста в выборке, а её цель {share * 100:.2f}%. "
                "Объедините категории или перенесите цель в соседнюю ячейку."
            )
        if base > 0 and share == 0:
            raise WeightingError(
                f"У {base} респондентов ячейки «{label}» цель 0%: их вес был бы нулевым."
            )
        if base == 0:
            continue
        weight = share * size / base
        if (lower is not None and weight < float(lower)) or (
            upper is not None and weight > float(upper)
        ):
            raise WeightingError(
                f"Вес ячейки «{label}» равен {weight:.3f} и выходит за границы "
                f"{_bound(lower)}–{_bound(upper)}. Объедините её с соседней "
                "или ослабьте ограничения."
            )
        weights.loc[mask] = weight
        cells.append(
            {
                "label": label,
                "base": base,
                "before_percent": base / size * 100,
                "target_percent": share * 100,
                "weight": weight,
            }
        )
    if targets:
        unknown = next(iter(targets))
        raise WeightingError(f"Ячейки «{' × '.join(unknown)}» нет среди сочетаний категорий.")
    # Цель категории — сумма целей её ячеек: так диагностика измерений
    # показывает то же «до → после · цель», что у raking.
    total = float(weights.sum())
    for dimension in prepared:
        for category in dimension["categories"]:
            category["target_share"] = float(weights[category["mask"]].sum()) / total
    diagnostics = _diagnostics(weights, prepared, 1, 0.0)
    diagnostics["cells"] = cells
    return RakingResult(
        weights=weights, iterations=1, maximum_deviation=0.0, diagnostics=diagnostics
    )


def _bound(value: Any) -> str:
    return "—" if value is None else f"{float(value):g}"


def _prepare_dimension(
    frame: pd.DataFrame, definition: dict[str, Any], *, shares: bool = True
) -> dict[str, Any]:
    """Категории измерения с масками; `shares=False` — без целевых долей.

    У взвешивания по ячейкам цель задаётся сочетанию категорий, а не
    категории, поэтому доли измерения там не нужны и не проверяются.
    """
    variable = definition.get("variable")
    if not variable or variable not in frame.columns:
        raise WeightingError("Переменная взвешивания не найдена в SAV.")
    targets = definition.get("targets", [])
    if len(targets) < 2:
        raise WeightingError("Целевое распределение должно содержать минимум две категории.")
    total_percent = 100.0
    if shares:
        if any(target.get("percent") is None for target in targets):
            raise WeightingError("Для raking задайте цель каждой категории распределения.")
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
                "target_share": (
                    float(target["percent"]) / total_percent if shares else 0.0
                ),
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
