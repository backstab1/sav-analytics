from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat


class BannerError(ValueError):
    pass


def validate_banner(definition: dict[str, Any], project: dict[str, Any]) -> None:
    blocks = definition["blocks"]
    if not blocks:
        raise BannerError("Добавьте хотя бы один блок баннера.")
    for block in blocks:
        sources = block["sources"]
        if not 1 <= len(sources) <= 2:
            raise BannerError("Блок баннера должен содержать один или два уровня.")
        for source in sources:
            _resolve_source(source, project)
    wave_mode = definition.get("wave_comparison", "none")
    wave_sources = [
        source
        for block in blocks
        for source in block["sources"]
        if _source_is_wave(source, project)
    ]
    if wave_mode != "none" and not wave_sources:
        raise BannerError("Для сравнения волн добавьте переменную с ролью «Волна» в баннер.")
    if wave_mode == "control" and definition.get("wave_control_value") is None:
        raise BannerError("Для контрольного сравнения выберите контрольную волну.")
    weight_variable = definition.get("weight_variable")
    if weight_variable and not any(
        item["name"] == weight_variable for item in project["inspection"]["variables"]
    ):
        raise BannerError("Весовая переменная не найдена в SAV.")
    calculated_weight_id = definition.get("calculated_weight_id")
    if calculated_weight_id and not any(
        item["id"] == str(calculated_weight_id)
        for item in project["configuration"].get("calculated_weights", [])
    ):
        raise BannerError("Рассчитанный вес не найден в проекте.")


def calculate_banner_preview(
    path: str | Path, definition: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    required = []
    for block in definition["blocks"]:
        for source in block["sources"]:
            variable = _source_variable(source, project)
            if variable not in required:
                required.append(variable)
    frame, _ = pyreadstat.read_sav(
        path,
        usecols=required,
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    built = build_banner_columns(frame, definition, project)
    columns = [
        {key: value for key, value in column.items() if key != "mask"}
        for column in built
    ]
    return {
        "id": definition.get("id"),
        "name": definition["name"],
        "total_base": len(frame),
        "columns": columns,
    }


def build_banner_columns(
    frame: pd.DataFrame, definition: dict[str, Any], project: dict[str, Any]
) -> list[dict[str, Any]]:
    validate_banner(definition, project)
    columns = [
        {
            "key": "total",
            "label": "Total",
            "path": ["Total"],
            "base": len(frame),
            "block": None,
            "mask": pd.Series(True, index=frame.index),
        }
    ]
    compare_to_total = definition.get("compare_to_total")
    if compare_to_total is None:
        compare_to_total = any(
            block.get("compare_to_total", False) for block in definition["blocks"]
        )
    compare_pairwise = definition.get("compare_pairwise")
    if compare_pairwise is None:
        compare_pairwise = any(
            block.get("compare_pairwise", False) for block in definition["blocks"]
        )
    for block_index, block in enumerate(definition["blocks"]):
        resolved = [_source_categories(source, project, frame) for source in block["sources"]]
        if len(resolved) == 1:
            combinations = ((category,) for category in resolved[0]["categories"])
        else:
            combinations = product(resolved[0]["categories"], resolved[1]["categories"])
        for combination in combinations:
            mask = pd.Series(True, index=frame.index)
            path_labels = []
            keys = []
            dimension_keys = []
            wave_value = None
            for category in combination:
                mask &= category["mask"]
                path_labels.append(category["label"])
                keys.append(category["key"])
                dimension_keys.append(category["key"] if not category["is_wave"] else None)
                if category["is_wave"]:
                    wave_value = category["value"]
            columns.append(
                {
                    "key": f"block-{block_index}:" + "|".join(keys),
                    "label": " · ".join(path_labels),
                    "path": path_labels,
                    "base": int(mask.sum()),
                    "block": block.get("label") or " → ".join(
                        source["label"] for source in resolved
                    ),
                    "block_index": block_index,
                    "compare_to_total": compare_to_total,
                    "compare_pairwise": compare_pairwise,
                    "wave_value": wave_value,
                    "wave_peer_key": tuple(dimension_keys),
                    "wave_comparison": definition.get("wave_comparison", "none"),
                    "mask": mask,
                }
            )
    if definition.get("wave_comparison") == "control" and not any(
        column.get("wave_value") is not None
        and _values_equal(column["wave_value"], definition.get("wave_control_value"))
        for column in columns
    ):
        raise BannerError("Выбранная контрольная волна отсутствует в баннере.")
    return columns


def _resolve_source(source: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    configuration = project["configuration"]
    if source["kind"] == "question":
        question = next(
            (item for item in configuration["questions"] if item["code"] == source["ref"]),
            None,
        )
        if question is None:
            raise BannerError("Вопрос для баннера не найден.")
        if question["question_type"] != "single_choice" or len(question["source_variables"]) != 1:
            raise BannerError("В баннер можно добавить только одиночный single choice.")
        return question
    if source["kind"] == "recoding":
        recoding = next(
            (item for item in configuration["recodings"] if item["id"] == source["ref"]),
            None,
        )
        if recoding is None:
            raise BannerError("Перекодировка для баннера не найдена.")
        return recoding
    raise BannerError("Неизвестный вид источника баннера.")


def _source_variable(source: dict[str, Any], project: dict[str, Any]) -> str:
    resolved = _resolve_source(source, project)
    if source["kind"] == "question":
        return resolved["source_variables"][0]
    return resolved["source_variable"]


def _source_categories(
    source: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> dict[str, Any]:
    resolved = _resolve_source(source, project)
    variable_name = _source_variable(source, project)
    series = frame[variable_name]
    if source["kind"] == "question":
        variable = next(
            item for item in project["inspection"]["variables"] if item["name"] == variable_name
        )
        values = [item["value"] for item in variable["value_labels"]]
        values.extend(
            value
            for value in series.dropna().unique()
            if not _contains_value(values, value)
        )
        labels = {str(item["value"]): item["label"] for item in variable["value_labels"]}
        categories = [
            {
                "key": f"question:{resolved['code']}:{_scalar(value)}",
                "label": labels.get(str(_scalar(value)), str(_scalar(value))),
                "value": _scalar(value),
                "is_wave": resolved.get("role") == "wave",
                "mask": series.map(lambda item, expected=value: _values_equal(item, expected)),
            }
            for value in values
        ]
        return {"label": resolved["label"], "categories": categories}

    categories = []
    for position, category in enumerate(resolved["categories"], start=1):
        if resolved.get("mode", "ranges") == "categories":
            values = tuple(category["values"])
            mask = series.map(
                lambda item, expected=values: any(
                    _values_equal(item, value) for value in expected
                )
            )
        else:
            numeric = pd.to_numeric(series, errors="coerce")
            mask = numeric.notna()
            if category.get("lower") is not None:
                mask &= numeric >= category["lower"]
            if category.get("upper") is not None:
                mask &= numeric <= category["upper"]
        categories.append(
            {
                "key": f"recoding:{resolved['id']}:{position}",
                "label": category["label"],
                "value": position,
                "is_wave": False,
                "mask": mask,
            }
        )
    return {"label": resolved["name"], "categories": categories}


def _source_is_wave(source: dict[str, Any], project: dict[str, Any]) -> bool:
    return source["kind"] == "question" and _resolve_source(source, project).get("role") == "wave"


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
