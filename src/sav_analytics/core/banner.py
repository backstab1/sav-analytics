from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd

from .filtering import conditional_series, recoding_columns
from .formulas import read_project_frame
from .multiple_response import (
    is_multiple,
    response_definition,
    response_options,
    selected_mask,
)


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
            for variable in _source_columns(source, project):
                if variable not in required:
                    required.append(variable)
    frame = read_project_frame(path, project, required)
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
        "overlaps": _block_overlaps(built),
    }


def source_category_options(
    path: str | Path, source: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    """Категории источника с базами — для настройки порядка, подписей и скрытия."""
    frame = read_project_frame(path, project, _source_columns(source, project))
    resolved = _source_categories(source, project, frame)
    return {
        "label": resolved["label"],
        "total_base": len(frame),
        "overlapping": bool(resolved.get("overlapping")),
        "categories": [
            {
                "key": category["key"],
                "label": category["label"],
                "base": int(category["mask"].sum()),
            }
            for category in resolved["categories"]
        ],
    }


def _configured_categories(source: dict[str, Any], resolved: dict[str, Any]) -> dict[str, Any]:
    """Применить настройки категорий баннера: порядок, подписи, скрытие.

    Настройка по ключу, которого больше нет в данных, молча пропускается: она
    ничего не показывает и не мешает. Новые категории, которых в настройке
    нет, выводятся в конце — так они не теряются незаметно.
    """
    settings = source.get("categories") or []
    if not settings:
        return resolved
    by_key = {category["key"]: category for category in resolved["categories"]}
    ordered = []
    seen = set()
    for setting in settings:
        category = by_key.get(setting["key"])
        if category is None or setting["key"] in seen:
            continue
        seen.add(setting["key"])
        if setting.get("hidden"):
            continue
        label = (setting.get("label") or "").strip()
        ordered.append({**category, "label": label or category["label"]})
    ordered.extend(
        category for category in resolved["categories"] if category["key"] not in seen
    )
    if not ordered:
        raise BannerError(f"В «{resolved['label']}» скрыты все категории.")
    groups = {
        setting["key"]: (setting.get("group") or "").strip()
        for setting in settings
        if (setting.get("group") or "").strip()
    }
    if groups:
        ordered = _merged_categories(ordered, groups, resolved["label"])
    return {**resolved, "categories": ordered}


def _merged_categories(
    categories: list[dict[str, Any]], groups: dict[str, str], source_label: str
) -> list[dict[str, Any]]:
    """Категории одной группы — одна колонка на месте первой из них.

    Колонка группы — респонденты любой из её категорий. У одиночного выбора и
    перекодировки категории не пересекаются, поэтому и после объединения
    колонки остаются непересекающимися, и схема Subgroup/Rest с попарными
    тестами применима без оговорок. Волны не объединяются: сравнение волн
    идёт по каждой волне отдельно, и сумма двух волн его бы подменила.
    """
    merged: list[dict[str, Any]] = []
    by_group: dict[str, dict[str, Any]] = {}
    for category in categories:
        group = groups.get(category["key"])
        if not group:
            merged.append(category)
            continue
        if category["is_wave"]:
            raise BannerError(f"Волны в «{source_label}» не объединяются в группы.")
        existing = by_group.get(group)
        if existing is None:
            existing = {
                "key": f"group:{group}",
                "label": group,
                "value": None,
                "is_wave": False,
                "mask": category["mask"].copy(),
            }
            by_group[group] = existing
            merged.append(existing)
        else:
            existing["mask"] = existing["mask"] | category["mask"]
    return merged


def _block_overlaps(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Сколько респондентов блока попали больше чем в одну колонку.

    Перекрытие видно в редакторе числом, а не обнаруживается по странным
    буквам в отчёте.
    """
    overlaps = []
    for block_index in sorted(
        {column["block_index"] for column in columns if column.get("overlapping")}
    ):
        members = [column for column in columns if column.get("block_index") == block_index]
        counts = pd.concat([column["mask"] for column in members], axis=1).sum(axis=1)
        overlaps.append(
            {
                "block_index": block_index,
                "block": members[0]["block"],
                "respondents": int((counts > 1).sum()),
            }
        )
    return overlaps


def banner_columns(definition: dict[str, Any], project: dict[str, Any]) -> set[str]:
    """Столбцы SAV, из которых строятся колонки баннера."""
    return {
        column
        for block in definition["blocks"]
        for source in block["sources"]
        for column in _source_columns(source, project)
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
    compare_target = definition.get("compare_target") or "rest"
    compare_pairwise = definition.get("compare_pairwise")
    if compare_pairwise is None:
        compare_pairwise = any(
            block.get("compare_pairwise", False) for block in definition["blocks"]
        )
    for block_index, block in enumerate(definition["blocks"]):
        resolved = [
            _configured_categories(source, _source_categories(source, project, frame))
            for source in block["sources"]
        ]
        overlapping = any(item.get("overlapping") for item in resolved)
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
                    "compare_target": compare_target,
                    "compare_pairwise": compare_pairwise,
                    # Колонки блока пересекаются: попарный тест для зависимых
                    # выборок не реализован, сравнение только с остальными.
                    "overlapping": overlapping,
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
        if _is_multiple(question):
            definition = response_definition(question)
            dichotomy = definition.get("encoding") == "dichotomy"
            if dichotomy and definition.get("counted_value") is None:
                raise BannerError(
                    "У multiple-response для баннера должен быть задан код выбранного ответа."
                )
            return question
        if question["question_type"] != "single_choice" or len(question["source_variables"]) != 1:
            raise BannerError(
                "В баннер можно добавить одиночный single choice или multiple-response."
            )
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


def _is_multiple(question: dict[str, Any]) -> bool:
    return is_multiple(question)


def _source_variable(source: dict[str, Any], project: dict[str, Any]) -> str:
    resolved = _resolve_source(source, project)
    if source["kind"] == "question":
        return resolved["source_variables"][0]
    return resolved["source_variable"]


def _source_columns(source: dict[str, Any], project: dict[str, Any]) -> list[str]:
    resolved = _resolve_source(source, project)
    if source["kind"] == "recoding" and resolved.get("mode") in {"conditions", "segments"}:
        return sorted(recoding_columns(resolved, project))
    if source["kind"] == "question" and _is_multiple(resolved):
        return list(resolved["source_variables"])
    return [_source_variable(source, project)]


def _source_categories(
    source: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> dict[str, Any]:
    resolved = _resolve_source(source, project)
    if source["kind"] == "recoding" and resolved.get("mode") in {"conditions", "segments"}:
        if resolved.get("mode") == "segments":
            from .segmentation import SegmentationError, segment_series

            try:
                labels = segment_series(resolved, project, frame)
            except SegmentationError as exc:
                raise BannerError(str(exc)) from exc
        else:
            labels = conditional_series(resolved, project, frame)
        return {
            "label": resolved["name"],
            "categories": [
                {
                    "key": f"recoding:{resolved['id']}:{position}",
                    "label": category["label"],
                    "value": position,
                    "is_wave": False,
                    "mask": labels.map(lambda item, expected=category["label"]: item == expected),
                }
                for position, category in enumerate(resolved["categories"], start=1)
            ],
        }
    if source["kind"] == "question" and _is_multiple(resolved):
        # Колонка на вариант: выбравшие его. Один респондент может выбрать
        # несколько вариантов, поэтому колонки пересекаются.
        variables = {item["name"]: item for item in project["inspection"]["variables"]}
        return {
            "label": resolved["label"],
            "overlapping": True,
            "categories": [
                {
                    "key": f"question:{resolved['code']}:{option['key']}",
                    "label": option["label"],
                    "value": option["key"],
                    "is_wave": False,
                    "mask": selected_mask(frame, resolved, option["key"]),
                }
                for option in response_options(resolved, variables, frame)
            ],
        }
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
