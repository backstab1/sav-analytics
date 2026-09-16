"""Выгрузка SAV проекта: исходный массив плюс всё, что построено в приложении.

Аналитик, который продолжает работу в SPSS, получает те же производные
переменные, что видел в отчёте: формулы, перекодировки и логические
переменные — кодами с подписями, рассчитанные веса — числовыми столбцами.
Исходные столбцы пишутся как есть, с объявленными пропусками SPSS; подпись
одиночного вопроса, исправленная в структуре, заменяет подпись переменной.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat

from .banner import BannerError, _source_categories
from .formulas import read_project_frame
from .weighting import WeightingError, calculate_raking


class SavExportError(ValueError):
    pass


@dataclass
class SavExportSummary:
    formulas: list[str] = field(default_factory=list)
    recodings: list[str] = field(default_factory=list)
    weights: list[str] = field(default_factory=list)


def export_project_sav(
    path: str | Path, project: dict[str, Any], destination: str | Path
) -> SavExportSummary:
    raw, meta = pyreadstat.read_sav(
        path,
        apply_value_formats=False,
        user_missing=True,
        dates_as_pandas_datetime=False,
    )
    # Производные считаются, как в отчёте: объявленные пропуски — пустые.
    computed = read_project_frame(path, project)
    configuration = project["configuration"]
    labels: dict[str, str | None] = dict(meta.column_names_to_labels)
    value_labels: dict[str, dict[Any, str]] = dict(meta.variable_value_labels)
    measures = {
        name: level
        for name, level in meta.variable_measure.items()
        if level in {"nominal", "ordinal", "scale"}
    }
    formats: dict[str, str] = {}
    summary = SavExportSummary()
    taken = {name.lower() for name in raw.columns}

    for question in configuration["questions"]:
        sources = question.get("source_variables") or []
        if len(sources) == 1 and sources[0] in labels and not question.get("formula_id"):
            labels[sources[0]] = question["label"]

    for formula in configuration.get("formulas", []):
        name = formula["name"]
        raw[name] = computed[name]
        taken.add(name.lower())
        labels[name] = formula["label"]
        measures[name] = "scale"
        formats[name] = "F16.4"
        summary.formulas.append(name)

    for recoding in configuration.get("recodings", []):
        try:
            resolved = _source_categories(
                {"kind": "recoding", "ref": recoding["id"]}, project, computed
            )
        except BannerError as exc:
            raise SavExportError(f"Перекодировка {recoding['code']}: {exc}") from exc
        series = pd.Series(float("nan"), index=computed.index)
        for category in resolved["categories"]:
            # Категории перекодировки не пересекаются; «первая подходящая» —
            # то же правило, что у логической переменной.
            series = series.where(series.notna() | ~category["mask"], float(category["value"]))
        name = _unique_name(recoding["code"], taken)
        raw[name] = series
        labels[name] = recoding.get("name") or recoding["code"]
        value_labels[name] = {
            float(category["value"]): category["label"] for category in resolved["categories"]
        }
        measures[name] = "nominal"
        formats[name] = "F8.0"
        summary.recodings.append(name)

    for weight in configuration.get("calculated_weights", []):
        try:
            result = calculate_raking(computed, weight)
        except WeightingError as exc:
            raise SavExportError(f"Вес «{weight['name']}»: {exc}") from exc
        name = _unique_name("W_" + _identifier(weight["name"]), taken)
        raw[name] = result.weights
        labels[name] = f"{weight['name']} (raking)"
        measures[name] = "scale"
        formats[name] = "F16.6"
        summary.weights.append(name)

    missing = {
        name: [
            item if not isinstance(item, dict) or item.get("lo") != item.get("hi") else item["lo"]
            for item in ranges
        ]
        for name, ranges in (meta.missing_ranges or {}).items()
        if name in raw.columns
    }
    pyreadstat.write_sav(
        raw,
        destination,
        column_labels={name: labels.get(name) for name in raw.columns},
        variable_value_labels=value_labels,
        missing_ranges=missing,
        variable_measure=measures,
        variable_format=formats or None,
    )
    return summary


def _identifier(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")
    return cleaned[:40] or "WEIGHT"


def _unique_name(base: str, taken: set[str]) -> str:
    name = base[:60]
    if not re.match(r"^[A-Za-z]", name):
        name = f"V_{name}"
    candidate = name
    index = 2
    while candidate.lower() in taken:
        candidate = f"{name}_{index}"
        index += 1
    taken.add(candidate.lower())
    return candidate
