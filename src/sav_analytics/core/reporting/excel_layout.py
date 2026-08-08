from __future__ import annotations

import math
from collections.abc import Callable
from numbers import Number
from typing import Any

import pandas as pd

from ..filtering import evaluate_filter_frame
from ..multiple_response import answered_mask, selected_mask
from ..statistics import StatisticalTestResult, effective_sample_size
from .models import ReportError, StatisticalAuditEntry
from .statistics import (
    _balance_result,
    _mean_test,
    _pairwise_balance_note,
    _pairwise_mean_note,
    _pairwise_proportion_note,
    _proportion_test,
    _record_total_comparison,
    _record_wave_comparison,
    _StatisticsAuditWriter,
    _unweighted_mean_context,
    _unweighted_mean_test,
    _unweighted_proportion_context,
    _unweighted_proportion_test,
    _wave_mean_test,
    _wave_proportion_test,
    _wave_target,
)
from .styles import _result_format


def _write_topline(
    sheet: Any,
    frame: pd.DataFrame,
    project: dict[str, Any],
    questions: list[dict[str, Any]],
    variables: dict[str, dict[str, Any]],
    filters: dict[str, dict[str, Any]],
    columns: list[dict[str, Any]],
    formats: dict[str, Any],
    statistical_settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    sheet_name: str,
    *,
    valid_denominator: bool,
    audit_writer: _StatisticsAuditWriter | None = None,
    advance: Callable[[str], None] | None = None,
) -> dict[str, int]:
    last_column = len(columns)
    sheet.hide_gridlines(2)
    sheet.freeze_panes(5, 1)
    sheet.set_column(0, 0, 46)
    sheet.set_column(1, last_column, 13)
    sheet.set_row(0, 24)
    sheet.write(0, 0, "Показатель", formats["banner"])
    for index, column in enumerate(columns, start=1):
        sheet.write(0, index, column.get("block") or "Общий итог", formats["banner"])
        sheet.write(1, index, column["label"], formats["banner"])
        sheet.write(2, index, _excel_column_name(index), formats["banner_letter"])
        sheet.write_number(3, index, column["base"], formats["base"])
    sheet.write(3, 0, "Невзвешенный N", formats["base_label"])
    sheet.set_row(4, 7)

    row = 5
    positions: dict[str, int] = {}
    for question in questions:
        audit_start = len(audit_entries)
        base_mask = pd.Series(True, index=frame.index)
        if question.get("base_filter_id"):
            definition = filters.get(question["base_filter_id"])
            if definition is None:
                raise ReportError(f"База вопроса {question['code']} не найдена.")
            base_mask &= evaluate_filter_frame(definition, project, frame)
        positions[question["code"]] = row + 1
        sheet.merge_range(
            row,
            0,
            row,
            last_column,
            f"{question['code']}. {question['label']}",
            formats["question"],
        )
        sheet.set_row(row, 30)
        row += 1
        row = _write_question_rows(
            sheet,
            row,
            frame,
            question,
            variables,
            columns,
            base_mask,
            formats,
            statistical_settings,
            audit_entries,
            sheet_name,
            valid_denominator,
        )
        if audit_writer is not None:
            audit_writer.write_entries(audit_entries[audit_start:])
            del audit_entries[audit_start:]
        if advance is not None:
            advance(f"{sheet_name}: {question['code']}")
        row += 1
    return positions

def _write_question_rows(
    sheet: Any,
    row: int,
    frame: pd.DataFrame,
    question: dict[str, Any],
    variables: dict[str, dict[str, Any]],
    columns: list[dict[str, Any]],
    base_mask: pd.Series,
    formats: dict[str, Any],
    statistical_settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    sheet_name: str,
    valid_denominator: bool,
) -> int:
    question_type = question["question_type"]
    sources = question["source_variables"]
    if question_type == "multiple_choice_dichotomy":
        answered = answered_mask(frame, question)
        for name in sources:
            selected = selected_mask(frame, question, name)
            row = _write_metric_row(
                sheet,
                row,
                variables[name]["label"],
                columns,
                base_mask,
                answered if valid_denominator else None,
                lambda mask, selected=selected: _ratio(selected[mask].sum(), mask.sum()),
                selected,
                formats,
                "percent",
                statistical_settings,
                audit_entries,
                (sheet_name, question["code"], question["label"]),
            )
        return row
    if question_type == "matrix":
        for name in sources:
            working = _scale_series(frame[name], question.get("special_values", []))
            sheet.write(row, 0, variables[name]["label"], formats["subquestion"])
            row += 1
            row = _write_distribution(
                sheet,
                row,
                frame[name],
                variables[name],
                columns,
                base_mask,
                formats,
                statistical_settings,
                audit_entries,
                (sheet_name, question["code"], question["label"]),
                valid_denominator,
            )
            row = _write_numeric_metric(
                sheet,
                row,
                "Среднее",
                working,
                columns,
                base_mask,
                formats,
                statistical_settings,
                audit_entries,
                (sheet_name, question["code"], question["label"]),
            )
            for aggregate_label, take_highest in (
                ("Top-2", True),
                ("Bottom-2", False),
            ):
                selected = _scale_aggregate(
                    working,
                    variables[name],
                    question.get("special_values", []),
                    take_highest=take_highest,
                )
                row = _write_metric_row(
                    sheet,
                    row,
                    aggregate_label,
                    columns,
                    base_mask,
                    working.notna() if valid_denominator else None,
                    lambda mask, selected=selected: _ratio(
                        selected[mask].sum(), mask.sum()
                    ),
                    selected,
                    formats,
                    "percent",
                    statistical_settings,
                    audit_entries,
                    (sheet_name, question["code"], question["label"]),
                )
        return row
    if len(sources) != 1:
        return row
    series = frame[sources[0]]
    if question_type == "numeric":
        metrics = [
            ("Среднее", "mean"),
            ("Медиана", "median"),
            ("Минимум", "min"),
            ("Максимум", "max"),
            ("Стандартное отклонение", "std"),
            ("Стандартная ошибка", "stderr"),
        ]
        for label, metric in metrics:
            row = _write_numeric_metric(
                sheet,
                row,
                label,
                series,
                columns,
                base_mask,
                formats,
                statistical_settings,
                audit_entries,
                (sheet_name, question["code"], question["label"]),
                metric,
            )
        return row
    row = _write_distribution(
        sheet,
        row,
        series,
        variables[sources[0]],
        columns,
        base_mask,
        formats,
        statistical_settings,
        audit_entries,
        (sheet_name, question["code"], question["label"]),
        valid_denominator,
    )
    if question_type == "scale":
        special_metric = question.get("special_metric", "none")
        if special_metric in {"nps", "csat"}:
            return _write_special_scale_rows(
                sheet,
                row,
                series,
                special_metric,
                columns,
                base_mask,
                formats,
                statistical_settings,
                audit_entries,
                (sheet_name, question["code"], question["label"]),
                valid_denominator,
            )
        working = _scale_series(series, question.get("special_values", []))
        row = _write_numeric_metric(
            sheet,
            row,
            "Среднее",
            working,
            columns,
            base_mask,
            formats,
            statistical_settings,
            audit_entries,
            (sheet_name, question["code"], question["label"]),
        )
        for aggregate_label, take_highest in (("Top-2", True), ("Bottom-2", False)):
            selected = _scale_aggregate(
                working,
                variables[sources[0]],
                question.get("special_values", []),
                take_highest=take_highest,
            )
            row = _write_metric_row(
                sheet,
                row,
                aggregate_label,
                columns,
                base_mask,
                working.notna() if valid_denominator else None,
                lambda mask, selected=selected: _ratio(
                    selected[mask].sum(), mask.sum()
                ),
                selected,
                formats,
                "percent",
                statistical_settings,
                audit_entries,
                (sheet_name, question["code"], question["label"]),
            )
    return row

def _write_special_scale_rows(
    sheet: Any,
    row: int,
    series: pd.Series,
    metric: str,
    columns: list[dict[str, Any]],
    base_mask: pd.Series,
    formats: dict[str, Any],
    settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    valid_denominator: bool,
) -> int:
    numeric = pd.to_numeric(series, errors="coerce")
    expected = set(range(11)) if metric == "nps" else set(range(1, 6))
    observed = set(numeric.dropna().unique())
    if not observed or not observed <= expected:
        label = "NPS 0–10" if metric == "nps" else "CSAT 1–5"
        raise ReportError(f"Вопрос {audit_context[1]} не соответствует шкале {label}.")
    valid_mask = numeric.notna() if valid_denominator else None
    if metric == "nps":
        groups = [
            ("Критики (0–6)", numeric.between(0, 6)),
            ("Нейтралы (7–8)", numeric.between(7, 8)),
            ("Промоутеры (9–10)", numeric.between(9, 10)),
        ]
        score = pd.Series(0.0, index=series.index)
        score[numeric.between(0, 6)] = -1
        score[numeric.between(9, 10)] = 1
        balance_label = "NPS"
        method = "NPS z-test"
    else:
        groups = [
            ("Неудовлетворённые (1–2)", numeric.between(1, 2)),
            ("Нейтральные (3)", numeric.eq(3)),
            ("Удовлетворённые (4–5)", numeric.between(4, 5)),
        ]
        score = pd.Series(0.0, index=series.index)
        score[numeric.between(1, 2)] = -1
        score[numeric.between(4, 5)] = 1
        balance_label = "CSAT balance"
        method = "CSAT balance z-test"
    score[numeric.isna()] = math.nan if valid_denominator else 0
    for label, selected in groups:
        row = _write_metric_row(
            sheet,
            row,
            label,
            columns,
            base_mask,
            valid_mask,
            lambda mask, selected=selected: _ratio(selected[mask].sum(), mask.sum()),
            selected,
            formats,
            "percent",
            settings,
            audit_entries,
            audit_context,
        )
    row = _write_balance_metric_row(
        sheet,
        row,
        balance_label,
        score,
        columns,
        base_mask & numeric.notna() if valid_denominator else base_mask,
        formats,
        settings,
        audit_entries,
        audit_context,
        method,
    )
    if metric == "csat":
        satisfied = numeric.between(4, 5)
        row = _write_metric_row(
            sheet,
            row,
            "% удовлетворённых",
            columns,
            base_mask,
            valid_mask,
            lambda mask: _ratio(satisfied[mask].sum(), mask.sum()),
            satisfied,
            formats,
            "percent",
            settings,
            audit_entries,
            audit_context,
        )
    return row

def _write_distribution(
    sheet: Any,
    row: int,
    series: pd.Series,
    variable: dict[str, Any],
    columns: list[dict[str, Any]],
    base_mask: pd.Series,
    formats: dict[str, Any],
    statistical_settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    valid_denominator: bool,
) -> int:
    labels = {str(item["value"]): item["label"] for item in variable["value_labels"]}
    values = [item["value"] for item in variable["value_labels"]]
    values.extend(value for value in series.dropna().unique() if str(value) not in labels)
    try:
        values = sorted(values, key=float)
    except (TypeError, ValueError):
        pass
    for value in values:
        selected = _equal_series(series, value)
        row = _write_metric_row(
            sheet,
            row,
            labels.get(str(value), str(value)),
            columns,
            base_mask,
            series.notna() if valid_denominator else None,
            lambda mask, selected=selected: _ratio(selected[mask].sum(), mask.sum()),
            selected,
            formats,
            "percent",
            statistical_settings,
            audit_entries,
            audit_context,
        )
    return row

def _write_metric_row(
    sheet: Any,
    row: int,
    label: str,
    columns: list[dict[str, Any]],
    base_mask: pd.Series,
    valid_mask: pd.Series | None,
    calculator: Any,
    outcome: pd.Series,
    formats: dict[str, Any],
    format_family: str,
    statistical_settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
) -> int:
    sheet.write(row, 0, label, formats[format_family])
    eligible_mask = base_mask if valid_mask is None else base_mask & valid_mask
    total_mask = columns[0]["mask"]
    pairwise_cache: dict[tuple[int, int], StatisticalTestResult | None] = {}
    weights = statistical_settings["weights"]
    vectorized = (
        _unweighted_proportion_context(outcome, eligible_mask, columns)
        if weights is None
        else None
    )
    for index, column in enumerate(columns, start=1):
        position = index - 1
        mask = column["mask"] & eligible_mask if vectorized is None else None
        if vectorized is None:
            value = _weighted_ratio(outcome, mask, weights)
            base = int(mask.sum())
            result = _proportion_test(
                outcome,
                total_mask,
                column,
                eligible_mask,
                columns,
                statistical_settings,
            )
        else:
            base = vectorized.bases[position]
            value = _ratio(vectorized.successes[position], base)
            result = _unweighted_proportion_test(
                vectorized,
                position,
                column,
                columns,
                statistical_settings,
            )
        _record_total_comparison(
            audit_entries, audit_context, label, column, columns, result
        )
        wave_target, wave_result = _wave_proportion_test(
            outcome, column, eligible_mask, columns, statistical_settings
        )
        _record_wave_comparison(
            audit_entries, audit_context, label, column, columns, wave_target, wave_result
        )
        cell_format = _result_format(
            formats,
            format_family,
            base,
            result,
            statistical_settings,
            wave_result,
        )
        if value is None:
            sheet.write_blank(row, index, None, cell_format)
        else:
            sheet.write_number(row, index, value * 100, cell_format)
        note = _pairwise_proportion_note(
            outcome,
            column,
            eligible_mask,
            columns,
            statistical_settings,
            audit_entries,
            audit_context,
            label,
            pairwise_cache,
            vectorized,
        )
        if note:
            sheet.write_comment(row, index, note, {"author": "sav-analytics"})
    return row + 1

def _write_numeric_metric(
    sheet: Any,
    row: int,
    label: str,
    series: pd.Series,
    columns: list[dict[str, Any]],
    base_mask: pd.Series,
    formats: dict[str, Any],
    statistical_settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    metric: str = "mean",
) -> int:
    sheet.write(row, 0, label, formats["mean"])
    pairwise_cache: dict[tuple[int, int], StatisticalTestResult | None] = {}
    weights = statistical_settings["weights"]
    mean_context = (
        _unweighted_mean_context(series, base_mask, columns)
        if metric == "mean" and weights is None
        else None
    )
    for index, column in enumerate(columns, start=1):
        mask = column["mask"] & base_mask
        numeric = pd.to_numeric(series[mask], errors="coerce").dropna()
        numeric_weights = weights.loc[numeric.index] if weights is not None else None
        value: float | None
        if numeric.empty:
            value = None
        elif numeric_weights is not None:
            value = _weighted_numeric_metric(numeric, numeric_weights, metric)
        elif metric == "mean":
            value = float(numeric.mean())
        elif metric == "median":
            value = float(numeric.median())
        elif metric == "min":
            value = float(numeric.min())
        elif metric == "max":
            value = float(numeric.max())
        elif metric == "std":
            value = float(numeric.std(ddof=1)) if len(numeric) > 1 else None
        else:
            std = float(numeric.std(ddof=1)) if len(numeric) > 1 else None
            value = std / math.sqrt(len(numeric)) if std is not None else None
        result = None
        if metric == "mean":
            result = (
                _unweighted_mean_test(
                    mean_context,
                    index - 1,
                    column,
                    columns,
                    statistical_settings,
                )
                if mean_context is not None
                else _mean_test(
                    series,
                    columns[0]["mask"],
                    column,
                    base_mask,
                    columns,
                    statistical_settings,
                )
            )
            _record_total_comparison(
                audit_entries, audit_context, label, column, columns, result
            )
        wave_target = None
        wave_result = None
        if metric == "mean":
            wave_target, wave_result = _wave_mean_test(
                series, column, base_mask, columns, statistical_settings
            )
            _record_wave_comparison(
                audit_entries,
                audit_context,
                label,
                column,
                columns,
                wave_target,
                wave_result,
            )
        cell_format = _result_format(
            formats, "mean", len(numeric), result, statistical_settings, wave_result
        )
        if value is None or not math.isfinite(value):
            sheet.write_blank(row, index, None, cell_format)
        else:
            sheet.write_number(row, index, value, cell_format)
        if metric == "mean":
            note = _pairwise_mean_note(
                series,
                column,
                base_mask,
                columns,
                statistical_settings,
                audit_entries,
                audit_context,
                label,
                pairwise_cache,
                mean_context,
            )
            if note:
                sheet.write_comment(row, index, note, {"author": "sav-analytics"})
    return row + 1

def _write_balance_metric_row(
    sheet: Any,
    row: int,
    label: str,
    scores: pd.Series,
    columns: list[dict[str, Any]],
    eligible_mask: pd.Series,
    formats: dict[str, Any],
    settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    method: str,
) -> int:
    sheet.write(row, 0, label, formats["percent"])
    total_mask = columns[0]["mask"] & eligible_mask
    pairwise_cache: dict[tuple[int, int], StatisticalTestResult | None] = {}
    for index, column in enumerate(columns, start=1):
        current_mask = column["mask"] & eligible_mask
        weights = settings["weights"]
        if not current_mask.any():
            value = None
        elif weights is None:
            value = float(scores[current_mask].mean())
        else:
            value = float(
                (scores[current_mask] * weights[current_mask]).sum()
                / weights[current_mask].sum()
            )
        total_result = None
        if column.get("compare_to_total"):
            total_result = _balance_result(
                scores,
                current_mask,
                total_mask & ~column["mask"],
                settings,
                columns,
                column,
                method,
            )
            _record_total_comparison(
                audit_entries, audit_context, label, column, columns, total_result
            )
        wave_target = _wave_target(column, columns, settings)
        wave_result = None
        if wave_target is not None:
            wave_result = _balance_result(
                scores,
                current_mask,
                wave_target["mask"] & eligible_mask,
                settings,
                columns,
                column,
                method,
            )
            _record_wave_comparison(
                audit_entries,
                audit_context,
                label,
                column,
                columns,
                wave_target,
                wave_result,
            )
        pairwise_note = _pairwise_balance_note(
            scores,
            column,
            eligible_mask,
            columns,
            settings,
            audit_entries,
            audit_context,
            label,
            method,
            pairwise_cache,
        )
        cell_format = _result_format(
            formats,
            "percent",
            int(current_mask.sum()),
            total_result,
            settings,
            wave_result,
        )
        if value is None:
            sheet.write_blank(row, index, None, cell_format)
        else:
            sheet.write_number(row, index, value * 100, cell_format)
        if pairwise_note:
            sheet.write_comment(row, index, pairwise_note, {"author": "sav-analytics"})
    return row + 1

def _write_contents(
    sheet: Any,
    project: dict[str, Any],
    questions: list[dict[str, Any]],
    main_rows: dict[str, int],
    filter_rows: dict[str, int],
    formats: dict[str, Any],
) -> None:
    sheet.hide_gridlines(2)
    sheet.set_column(0, 0, 14)
    sheet.set_column(1, 1, 60)
    sheet.set_column(2, 2, 20)
    sheet.merge_range("A1:C1", f"Содержание — {project['name']}", formats["title"])
    sheet.write_row("A3", ["Код", "Название", "Лист"], formats["contents_header"])
    row = 3
    for question in questions:
        code = question["code"]
        sheet.write_url(
            row,
            0,
            f"internal:'topline_main'!A{main_rows[code]}",
            formats["link"],
            code,
        )
        sheet.write(row, 1, question["label"])
        sheet.write(row, 2, "topline_main")
        row += 1
    if filter_rows:
        row += 1
        sheet.write_row(row, 0, ["Код", "Фильтровые вопросы", "Лист"], formats["contents_header"])
        row += 1
        by_code = {item["code"]: item for item in questions}
        for code, target_row in filter_rows.items():
            sheet.write_url(
                row,
                0,
                f"internal:'topline_filter'!A{target_row}",
                formats["link"],
                code,
            )
            sheet.write(row, 1, by_code[code]["label"])
            sheet.write(row, 2, "topline_filter")
            row += 1
    sheet.freeze_panes(3, 0)

def _excel_column_name(index: int) -> str:
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result

def _ratio(numerator: Any, denominator: int) -> float | None:
    return float(numerator) / denominator if denominator else None

def _weighted_ratio(outcome: pd.Series, mask: pd.Series, weights: pd.Series) -> float | None:
    selected_weights = weights[mask]
    if selected_weights.empty:
        return None
    return float(weights[mask & outcome.fillna(False).astype(bool)].sum() / selected_weights.sum())

def _weighted_numeric_metric(
    values: pd.Series, weights: pd.Series, metric: str
) -> float | None:
    weight_sum = float(weights.sum())
    mean = float((values * weights).sum() / weight_sum)
    if metric == "mean":
        return mean
    if metric == "median":
        ordered = pd.DataFrame({"value": values, "weight": weights}).sort_values("value")
        cumulative = ordered["weight"].cumsum()
        return float(ordered.loc[cumulative >= weight_sum / 2, "value"].iloc[0])
    if metric == "min":
        return float(values.min())
    if metric == "max":
        return float(values.max())
    denominator = weight_sum - float((weights**2).sum()) / weight_sum
    if denominator <= 0:
        return None
    variance = float((weights * (values - mean) ** 2).sum() / denominator)
    if metric == "std":
        return math.sqrt(variance)
    return math.sqrt(variance / effective_sample_size(weights))

def _equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right) or str(left) == str(right)
    except (TypeError, ValueError):
        return False

def _equal_series(series: pd.Series, expected: Any) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series.dtype) and isinstance(expected, Number):
        return series.eq(expected).fillna(False)
    return series.map(lambda item: _equal(item, expected))


def _scale_series(series: pd.Series, special_values: list[Any]) -> pd.Series:
    working = series.copy()
    for value in special_values:
        working = working.mask(_equal_series(working, value))
    return pd.to_numeric(working, errors="coerce")


def _scale_aggregate(
    series: pd.Series,
    variable: dict[str, Any],
    special_values: list[Any],
    *,
    take_highest: bool,
) -> pd.Series:
    raw_values = [item["value"] for item in variable.get("value_labels", [])]
    raw_values.extend(series.dropna().unique())
    codes: set[float] = set()
    for value in raw_values:
        if any(_equal(value, special) for special in special_values):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            codes.add(numeric)
    ordered = sorted(codes)
    selected_codes = ordered[-2:] if take_highest else ordered[:2]
    if len(selected_codes) == 2:
        return series.isin(selected_codes)
    return pd.Series(False, index=series.index)

