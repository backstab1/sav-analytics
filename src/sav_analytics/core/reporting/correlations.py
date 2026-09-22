"""Лист `Correlations`: связи числовых вопросов отчёта между собой.

Строится теми же функциями, что карточки раздела «Анализ»
(`core/association.py`): метод выбирается по данным — Пирсон, а при выбросах
ранговый Спирмен, — и p-value всех пар корректируется Benjamini–Hochberg.
Поэтому число на листе не может разойтись с числом в карточке.

Учитывается общий фильтр отчёта и вес: взвешенный Пирсон через взвешенную
ковариацию, Спирмен — по взвешенным рангам, p-value приближённый по
эффективной базе Киша (`requirements.md` §11).
"""

from __future__ import annotations

from typing import Any

from ..association import (
    AssociationError,
    adjust_benjamini_hochberg,
    numeric_correlation,
    resolve_variable,
)
from .data import ReportData
from .styles import ReportFormats

NUMERIC_TYPES = {"numeric", "scale"}
MAX_VARIABLES = 40


def correlation_matrix(data: ReportData, project: dict[str, Any]) -> dict[str, Any]:
    """Матрица корреляций числовых вопросов отчёта с протоколом по каждой паре."""
    questions = [
        question
        for question in data.questions
        if question["question_type"] in NUMERIC_TYPES
        and len(question.get("source_variables") or []) == 1
    ][:MAX_VARIABLES]
    rows = data.columns[0]["mask"]
    variables = []
    for question in questions:
        try:
            variables.append(
                resolve_variable({"kind": "question", "ref": question["code"]}, project, data.frame)
            )
        except AssociationError:
            continue
    variables = [variable for variable in variables if variable.kind == "numeric"]
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    results = []
    for left in range(len(variables)):
        for right in range(left + 1, len(variables)):
            result = numeric_correlation(
                variables[left], variables[right], rows, data.statistical_settings["weights"]
            )
            cells[(left, right)] = result
            results.append(result)
    adjust_benjamini_hochberg(results)
    return {
        "labels": [variable.label for variable in variables],
        "cells": cells,
        "weighted": data.statistical_settings["weights"] is not None,
    }


def write_correlations(
    sheet: Any, matrix: dict[str, Any], formats: ReportFormats
) -> None:
    labels = matrix["labels"]
    sheet.hide_gridlines(2)
    sheet.set_column(0, 0, 44)
    sheet.set_column(1, max(1, len(labels)), 12)
    sheet.set_row(0, 30)
    sheet.write(0, 0, "Correlations", formats.title())
    lead = (
        "Пирсон, при выбросах — ранговый Спирмен. Полужирным — значимые после "
        "поправки Benjamini–Hochberg на все пары листа."
    )
    if matrix["weighted"]:
        lead += (
            " Взвешено: коэффициенты взвешенные, p-value приближённый по эффективной"
            " базе Киша."
        )
    sheet.write(1, 0, lead, formats.meta())
    if not labels:
        sheet.write(3, 0, "Числовых вопросов в отчёте нет.", formats.meta())
        return
    header_row = 3
    for index, label in enumerate(labels):
        sheet.write(header_row, index + 1, _short(label), formats.column_label())
        sheet.write(header_row + 1 + index, 0, label, formats.row_label())
    for row in range(len(labels)):
        for column in range(len(labels)):
            target = header_row + 1 + row, column + 1
            if row == column:
                sheet.write_string(*target, "—", formats.absent())
                continue
            result = matrix["cells"].get((min(row, column), max(row, column)))
            if result is None or not result.get("performed"):
                sheet.write_string(*target, "–", formats.absent())
                note = (result or {}).get("reason") or "Пара не посчитана."
                sheet.write_comment(*target, note)
                continue
            sheet.write_number(
                *target,
                result["statistic"],
                formats.correlation(significant=bool(result.get("significant"))),
            )
            sheet.write_comment(*target, _protocol(result))


def _short(label: str) -> str:
    return label.split(" ", 1)[0]


def _protocol(result: dict[str, Any]) -> str:
    lines = [
        f"Метод: {result['method']}",
        f"N пар: {result['n']}",
        f"r = {result['statistic']:.4f}".replace(".", ","),
        f"p-value: {result['p_value']:.6f}".replace(".", ","),
        f"p с поправкой BH: {result['p_adjusted']:.6f}".replace(".", ","),
        f"Решение: {'значимо' if result['significant'] else 'незначимо'}",
        f"Сила связи: {result['magnitude']}",
    ]
    if result.get("note_method"):
        lines.append(result["note_method"])
    return "\n".join(lines)
