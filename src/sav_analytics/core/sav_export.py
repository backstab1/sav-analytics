"""Выгрузка SAV проекта: исходный массив плюс всё, что построено в приложении.

Аналитик, который продолжает работу в SPSS, получает те же производные
переменные, что видел в отчёте: формулы, перекодировки и логические
переменные — кодами с подписями, рассчитанные веса — числовыми столбцами.
Исходные столбцы пишутся как есть, с объявленными пропусками SPSS; подпись
одиночного вопроса, исправленная в структуре, заменяет подпись переменной.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat

from .banner import BannerError, _source_categories
from .formulas import read_project_frame
from .sav_writing import SavWriteMismatchError, long_text_columns, verify_written_sav
from .weighting import WeightingError, calculate_weight, weight_method_label


class SavExportError(ValueError):
    def __init__(self, message: str, *, long_text: list[str] | None = None) -> None:
        super().__init__(message)
        #: Длинные тексты, из-за которых запись сломалась; их можно не выгружать.
        self.long_text = long_text or []


@dataclass
class SavExportSummary:
    formulas: list[str] = field(default_factory=list)
    recodings: list[str] = field(default_factory=list)
    weights: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)


def export_project_sav(
    path: str | Path,
    project: dict[str, Any],
    destination: str | Path,
    *,
    omit_long_text: bool = False,
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
            result = calculate_weight(computed, weight, project)
        except WeightingError as exc:
            raise SavExportError(f"Вес «{weight['name']}»: {exc}") from exc
        name = _unique_name("W_" + _identifier(weight["name"]), taken)
        raw[name] = result.weights
        labels[name] = f"{weight['name']} ({weight_method_label(weight)})"
        measures[name] = "scale"
        formats[name] = "F16.6"
        summary.weights.append(name)

    long_text = long_text_columns(raw)
    note = None
    if omit_long_text and long_text:
        raw = raw.drop(columns=long_text)
        summary.omitted = long_text
        # Строка заметки SAV — не длиннее 80 байт, кириллица занимает по два.
        note = textwrap.wrap(
            "Не выгружены длинные тексты (больше 255 байт): " + ", ".join(long_text), 38
        )
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
        variable_value_labels={
            name: value for name, value in value_labels.items() if name in raw.columns
        },
        missing_ranges=missing,
        variable_measure={
            name: value for name, value in measures.items() if name in raw.columns
        },
        variable_format={name: value for name, value in formats.items() if name in raw.columns}
        or None,
        note=note,
    )
    try:
        verify_written_sav(destination, list(raw.columns))
    except SavWriteMismatchError as exc:
        if long_text and not omit_long_text:
            raise SavExportError(
                "Текстовые переменные " + ", ".join(long_text[:12])
                + (" и другие" if len(long_text) > 12 else "")
                + " длиннее 255 байт. Библиотека записи SAV делит такие строки на части "
                "и называет части так же, как соседние переменные, поэтому файл выходит "
                "испорченным. Выгрузите SAV без длинных текстов — они остаются в "
                "исходном файле проекта.",
                long_text=long_text,
            ) from exc
        raise SavExportError(f"SAV записан с ошибкой: {exc}") from exc
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
