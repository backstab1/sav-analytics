from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat

from .multiple_response import (
    MultipleResponseError,
    answered_mask,
    selected_mask,
)


class FilterError(ValueError):
    pass


def validate_filter(definition: dict[str, Any], project: dict[str, Any]) -> None:
    _validate_group(definition["rule"], project, depth=1)


def calculate_filter_preview(
    path: str | Path, definition: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    validate_filter(definition, project)
    columns = sorted(_required_columns(definition["rule"], project))
    frame, _ = pyreadstat.read_sav(
        path,
        usecols=columns,
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    mask, steps = _evaluate_group(definition["rule"], project, frame)
    total = len(frame)
    selected = int(mask.sum())
    return {
        "id": definition.get("id"),
        "name": definition["name"],
        "total": total,
        "selected": selected,
        "share": selected / total if total else 0,
        "empty": selected == 0,
        "small_base": 0 < selected < 30,
        "steps": steps,
        "description": _describe_group(definition["rule"], project),
    }


def evaluate_filter_frame(
    definition: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> pd.Series:
    """Return a boolean mask for an already loaded SAV frame."""
    validate_filter(definition, project)
    mask, _ = _evaluate_group(definition["rule"], project, frame)
    return mask


def _validate_group(group: dict[str, Any], project: dict[str, Any], depth: int) -> None:
    if depth > 2:
        raise FilterError("Вложенность фильтра не может превышать два уровня.")
    if not group.get("items"):
        raise FilterError("Добавьте хотя бы одно условие.")
    for item in group["items"]:
        if item["kind"] == "group":
            _validate_group(item, project, depth + 1)
        else:
            _validate_condition(item, project)


def _validate_condition(condition: dict[str, Any], project: dict[str, Any]) -> None:
    source = _resolve_source(condition["source"], project)
    operator = condition["operator"]
    multiple_ops = {"selected", "selected_any", "selected_all", "selected_none"}
    is_multiple = source.get("question_type", "").startswith("multiple_choice")
    if operator in multiple_ops and not is_multiple:
        raise FilterError("Операция выбора вариантов доступна только для multiple-response.")
    if is_multiple and operator not in multiple_ops | {"filled", "missing"}:
        raise FilterError("Для multiple-response выберите операцию по вариантам.")
    if operator in {"eq", "ne", "in", "not_in"} and not condition.get("values"):
        raise FilterError("Укажите значение условия.")
    if operator in multiple_ops and not condition.get("values"):
        raise FilterError("Выберите хотя бы один вариант multiple-response.")
    if operator == "between" and (
        condition.get("lower") is None or condition.get("upper") is None
    ):
        raise FilterError("Для диапазона укажите обе границы.")
    if operator == "gt" and condition.get("lower") is None:
        raise FilterError("Укажите нижнюю границу.")
    if operator == "lt" and condition.get("upper") is None:
        raise FilterError("Укажите верхнюю границу.")


def _resolve_source(source: dict[str, str], project: dict[str, Any]) -> dict[str, Any]:
    key = "questions" if source["kind"] == "question" else "recodings"
    field = "code" if source["kind"] == "question" else "id"
    resolved = next(
        (item for item in project["configuration"][key] if item[field] == source["ref"]),
        None,
    )
    if resolved is None:
        raise FilterError("Источник условия не найден.")
    return resolved


def _required_columns(group: dict[str, Any], project: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in group["items"]:
        if item["kind"] == "group":
            result |= _required_columns(item, project)
            continue
        resolved = _resolve_source(item["source"], project)
        if item["source"]["kind"] == "question":
            result.update(resolved["source_variables"])
        else:
            result.add(resolved["source_variable"])
    return result


def _evaluate_group(
    group: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> tuple[pd.Series, list[dict[str, Any]]]:
    masks: list[pd.Series] = []
    steps: list[dict[str, Any]] = []
    for item in group["items"]:
        if item["kind"] == "group":
            mask, nested_steps = _evaluate_group(item, project, frame)
            steps.extend(nested_steps)
        else:
            mask = _evaluate_condition(item, project, frame)
        masks.append(mask)
        steps.append({"description": _describe_item(item, project), "selected": int(mask.sum())})
    result = masks[0].copy()
    for mask in masks[1:]:
        result = result & mask if group["operator"] == "and" else result | mask
    return result.fillna(False), steps


def _evaluate_condition(
    condition: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> pd.Series:
    resolved = _resolve_source(condition["source"], project)
    operator = condition["operator"]
    if condition["source"]["kind"] == "question" and resolved.get(
        "question_type", ""
    ).startswith("multiple_choice"):
        try:
            selected = [
                selected_mask(frame, resolved, name)
                for name in condition.get("values", [])
            ]
            available = answered_mask(frame, resolved)
        except MultipleResponseError as exc:
            raise FilterError(str(exc)) from exc
        if operator in {"selected", "selected_any"}:
            return pd.concat(selected, axis=1).any(axis=1)
        if operator == "selected_all":
            return pd.concat(selected, axis=1).all(axis=1)
        if operator == "selected_none":
            return available & ~pd.concat(selected, axis=1).any(axis=1)
        return available if operator == "filled" else ~available

    series = _source_series(condition["source"], resolved, frame)
    values = condition.get("values", [])
    if operator == "filled":
        return series.notna()
    if operator == "missing":
        return series.isna()
    if operator in {"eq", "in"}:
        return series.map(lambda value: _matches_any(value, values))
    if operator in {"ne", "not_in"}:
        return series.notna() & ~series.map(lambda value: _matches_any(value, values))
    numeric = pd.to_numeric(series, errors="coerce")
    if operator == "gt":
        return numeric > condition["lower"]
    if operator == "lt":
        return numeric < condition["upper"]
    if operator == "between":
        return numeric.between(condition["lower"], condition["upper"], inclusive="both")
    raise FilterError("Неизвестная операция фильтра.")


def _source_series(
    source: dict[str, str], resolved: dict[str, Any], frame: pd.DataFrame
) -> pd.Series:
    if source["kind"] == "question":
        if len(resolved["source_variables"]) != 1:
            raise FilterError("Для этого условия нужен одиночный вопрос.")
        return frame[resolved["source_variables"][0]]
    series = frame[resolved["source_variable"]]
    result = pd.Series(pd.NA, index=series.index, dtype="object")
    for category in resolved["categories"]:
        if resolved.get("mode", "ranges") == "categories":
            category_values = category["values"]
            mask = series.map(
                lambda value, expected=category_values: _matches_any(value, expected)
            )
        else:
            numeric = pd.to_numeric(series, errors="coerce")
            mask = numeric.notna()
            if category.get("lower") is not None:
                mask &= numeric >= category["lower"]
            if category.get("upper") is not None:
                mask &= numeric <= category["upper"]
        result.loc[mask] = category["label"]
    return result


def _matches_any(value: Any, expected: list[Any]) -> bool:
    if pd.isna(value):
        return False
    return any(value == item or str(value) == str(item) for item in expected)


def _describe_group(group: dict[str, Any], project: dict[str, Any]) -> str:
    separator = " И " if group["operator"] == "and" else " ИЛИ "
    parts = [_describe_item(item, project) for item in group["items"]]
    return separator.join(parts)


def _describe_item(item: dict[str, Any], project: dict[str, Any]) -> str:
    if item["kind"] == "group":
        return f"({_describe_group(item, project)})"
    resolved = _resolve_source(item["source"], project)
    label = resolved.get("label") or resolved.get("name") or item["source"]["ref"]
    operator_labels = {
        "eq": "=",
        "ne": "≠",
        "in": "входит в",
        "not_in": "не входит в",
        "gt": ">",
        "lt": "<",
        "between": "между",
        "filled": "заполнено",
        "missing": "пропущено",
        "selected": "выбран вариант",
        "selected_any": "выбран хотя бы один",
        "selected_all": "выбраны все",
        "selected_none": "не выбран ни один",
    }
    suffix = ""
    if item.get("values"):
        suffix = " " + ", ".join(map(str, item["values"]))
    elif item["operator"] == "between":
        suffix = f" {item['lower']}–{item['upper']}"
    elif item["operator"] == "gt":
        suffix = f" {item['lower']}"
    elif item["operator"] == "lt":
        suffix = f" {item['upper']}"
    return f"{label} {operator_labels[item['operator']]}{suffix}"
