"""Локальный просмотр строк массива без попадания данных в отчёт или аудит."""

from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from .filtering import evaluate_filter_frame, filter_required_columns
from .formulas import read_project_frame

MAX_COLUMNS = 25
MAX_CELL_TEXT = 2_000


class DataRowsError(ValueError):
    pass


def browse_data_rows(
    path: str | Path,
    project: dict[str, Any],
    *,
    columns: list[str] | None = None,
    filter_id: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Вернуть страницу исходных значений в порядке строк массива."""
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    wanted = list(dict.fromkeys(columns or list(variables)[:MAX_COLUMNS]))
    if not wanted:
        raise DataRowsError("В проекте нет столбцов для просмотра.")
    if len(wanted) > MAX_COLUMNS:
        raise DataRowsError(f"За один раз можно показать не больше {MAX_COLUMNS} столбцов.")
    missing = [name for name in wanted if name not in variables]
    if missing:
        raise DataRowsError("Столбцы не найдены: " + ", ".join(missing) + ".")

    definition = None
    required = list(wanted)
    if filter_id:
        definition = next(
            (
                item
                for item in project["configuration"].get("filters", [])
                if str(item["id"]) == str(filter_id)
            ),
            None,
        )
        if definition is None:
            raise DataRowsError("Фильтр просмотра не найден.")
        required.extend(filter_required_columns(definition, project))

    frame = read_project_frame(path, project, list(dict.fromkeys(required)))
    source_total = len(frame)
    if definition is not None:
        frame = frame.loc[evaluate_filter_frame(definition, project, frame)]
    total = len(frame)
    page = frame.iloc[offset : offset + limit]
    metadata = [_column_metadata(name, variables[name]) for name in wanted]
    return {
        "source_total": source_total,
        "total": total,
        "offset": offset,
        "limit": limit,
        "columns": metadata,
        "rows": [
            {
                "number": _row_number(index),
                "values": [_cell(page.at[index, name], variables[name]) for name in wanted],
            }
            for index in page.index
        ],
    }


def _row_number(index: Any) -> int | str:
    """Preserve a human-facing one-based number for the usual RangeIndex."""
    try:
        return int(index) + 1
    except (TypeError, ValueError, OverflowError):
        return str(index)


def _column_metadata(name: str, variable: dict[str, Any]) -> dict[str, str]:
    return {"name": name, "label": str(variable.get("label") or name)}


def _cell(value: Any, variable: dict[str, Any]) -> dict[str, Any]:
    scalar = _scalar(value)
    if scalar is None:
        return {"raw": None, "display": "—", "label": None, "truncated": False}
    label = _value_label(scalar, variable.get("value_labels") or [])
    raw = str(scalar) if isinstance(scalar, (date, datetime, time, Decimal)) else scalar
    display = str(label if label is not None else raw)
    truncated = len(display) > MAX_CELL_TEXT
    if truncated:
        display = display[: MAX_CELL_TEXT - 1] + "…"
    return {"raw": raw, "display": display, "label": label, "truncated": truncated}


def _value_label(value: Any, labels: list[dict[str, Any]]) -> str | None:
    for item in labels:
        if _same(value, item["value"]):
            return str(item["label"])
    return None


def _same(left: Any, right: Any) -> bool:
    if left == right:
        return True
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _scalar(value: Any) -> Any:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value
