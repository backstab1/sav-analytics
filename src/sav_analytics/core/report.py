from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat
import xlsxwriter

from .banner import build_banner_columns
from .filtering import evaluate_filter_frame
from .statistics import (
    StatisticalTestResult,
    balance_z_test,
    effective_sample_size,
    proportion_z_test,
    subgroup_vs_rest_z_test,
    weighted_proportion_z_test,
    weighted_welch_t_test,
    welch_t_test,
)
from .weighting import WeightingError, calculate_raking


class ReportError(ValueError):
    pass


@dataclass(frozen=True)
class ToplineArtifacts:
    xlsx: bytes
    statistics_txt: str


@dataclass(frozen=True)
class StatisticalAuditEntry:
    sheet: str
    question_code: str
    question_label: str
    row_label: str
    comparison: str
    group_a: str
    group_b: str
    result: StatisticalTestResult | None
    reason: str | None = None


def build_topline_xlsx(path: str | Path, project: dict[str, Any]) -> bytes:
    return build_topline_artifacts(path, project).xlsx


def build_statistics_txt(path: str | Path, project: dict[str, Any]) -> str:
    return build_topline_artifacts(path, project).statistics_txt


def build_topline_artifacts(path: str | Path, project: dict[str, Any]) -> ToplineArtifacts:
    frame, _ = pyreadstat.read_sav(
        path,
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    configuration = project["configuration"]
    global_mask = pd.Series(True, index=frame.index)
    report_filter_id = configuration.get("report_filter_id")
    if report_filter_id:
        definition = _find_by_id(configuration["filters"], report_filter_id, "Общий фильтр")
        global_mask &= evaluate_filter_frame(definition, project, frame)
        if not global_mask.any():
            raise ReportError("Общий фильтр отчёта даёт пустую выборку.")

    banners = configuration.get("banners", [])
    active_banner_id = configuration.get("report_banner_id")
    if "report_banner_id" not in configuration and banners:
        active_banner = banners[-1]
        columns = build_banner_columns(frame, active_banner, project)
    elif active_banner_id and banners:
        active_banner = next(
            (banner for banner in banners if banner.get("id") == active_banner_id),
            banners[-1],
        )
        columns = build_banner_columns(frame, active_banner, project)
    else:
        active_banner = {}
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
    for column in columns:
        column["mask"] = column["mask"] & global_mask
        column["base"] = int(column["mask"].sum())
    columns = [column for index, column in enumerate(columns) if index == 0 or column["base"]]

    questions = [
        question
        for question in configuration["questions"]
        if question["included_in_report"]
    ]
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    filters = {item["id"]: item for item in configuration.get("filters", [])}

    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties(
        {
            "title": f"Топлайн — {project['name']}",
            "subject": "Автоматически сформированный аналитический отчёт",
            "author": "sav-analytics",
        }
    )
    formats = _formats(workbook)
    audit_entries: list[StatisticalAuditEntry] = []
    weights, weight_label = _report_weights(
        frame,
        active_banner.get("weight_variable"),
        active_banner.get("calculated_weight_id"),
        configuration,
    )
    statistical_settings = {
        "confidence_level": active_banner.get("confidence_level", 0.95),
        "bonferroni": active_banner.get("bonferroni", False),
        "minimum_base": active_banner.get("minimum_base", 30),
        "weight_label": weight_label,
        "weights": weights,
        "wave_comparison": active_banner.get("wave_comparison", "none"),
        "wave_control_value": active_banner.get("wave_control_value"),
    }
    main = workbook.add_worksheet("topline_main")
    filtered = workbook.add_worksheet("topline_filter")
    contents = workbook.add_worksheet("Содержание")
    main_rows = _write_topline(
        main,
        frame,
        project,
        questions,
        variables,
        filters,
        columns,
        formats,
        statistical_settings,
        audit_entries,
        "topline_main",
        valid_denominator=False,
    )
    filter_questions = [question for question in questions if question["missing_count"] > 0]
    filter_rows = _write_topline(
        filtered,
        frame,
        project,
        filter_questions,
        variables,
        filters,
        columns,
        formats,
        statistical_settings,
        audit_entries,
        "topline_filter",
        valid_denominator=True,
    )
    _write_contents(contents, project, questions, main_rows, filter_rows, formats)
    workbook.close()
    statistics_txt = _render_statistics_txt(
        project,
        active_banner,
        configuration,
        statistical_settings,
        audit_entries,
    )
    return ToplineArtifacts(xlsx=output.getvalue(), statistics_txt=statistics_txt)


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
        answered = frame[sources].notna().any(axis=1)
        for name in sources:
            selected = frame[name].eq(1)
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
                frame[name],
                columns,
                base_mask,
                formats,
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
        row = _write_numeric_metric(
            sheet,
            row,
            "Среднее",
            series,
            columns,
            base_mask,
            formats,
            statistical_settings,
            audit_entries,
            (sheet_name, question["code"], question["label"]),
        )
        values = sorted(float(value) for value in series.dropna().unique())
        if len(values) >= 2:
            top_values = set(values[-2:])
            selected = series.isin(top_values)
            row = _write_metric_row(
                sheet,
                row,
                "Top-2",
                columns,
                base_mask,
                series.notna() if valid_denominator else None,
                lambda mask: _ratio(selected[mask].sum(), mask.sum()),
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
        selected = series.map(lambda item, expected=value: _equal(item, expected))
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
    for index, column in enumerate(columns, start=1):
        mask = column["mask"] & eligible_mask
        weights = statistical_settings["weights"]
        value = (
            _weighted_ratio(outcome, mask, weights)
            if weights is not None
            else calculator(mask)
        )
        result = _proportion_test(
            outcome,
            total_mask,
            column,
            eligible_mask,
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
            int(mask.sum()),
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
    for index, column in enumerate(columns, start=1):
        mask = column["mask"] & base_mask
        numeric = pd.to_numeric(series[mask], errors="coerce").dropna()
        weights = statistical_settings["weights"]
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
            result = _mean_test(
                series,
                columns[0]["mask"],
                column,
                base_mask,
                columns,
                statistical_settings,
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


def _balance_result(
    scores: pd.Series,
    mask_a: pd.Series,
    mask_b: pd.Series,
    settings: dict[str, Any],
    columns: list[dict[str, Any]],
    column: dict[str, Any],
    method: str,
) -> StatisticalTestResult | None:
    sample_a = scores[mask_a].dropna()
    sample_b = scores[mask_b].dropna()
    if sample_a.empty or sample_b.empty:
        return None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    weights = settings["weights"]
    return balance_z_test(
        sample_a,
        sample_b,
        weights_a=weights.loc[sample_a.index] if weights is not None else None,
        weights_b=weights.loc[sample_b.index] if weights is not None else None,
        method=method,
        confidence_level=settings["confidence_level"],
        comparisons=comparisons,
        minimum_base=settings["minimum_base"],
    )


def _pairwise_balance_note(
    scores: pd.Series,
    column: dict[str, Any],
    eligible_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
    method: str,
) -> str | None:
    if not column.get("compare_pairwise"):
        return None
    current_position = _column_position(columns, column)
    findings: list[tuple[str, str]] = []
    for position, other in enumerate(columns):
        if other is column or other.get("block_index") != column.get("block_index"):
            continue
        result = _balance_result(
            scores,
            column["mask"] & eligible_mask,
            other["mask"] & eligible_mask,
            settings,
            columns,
            column,
            method,
        )
        if current_position < position:
            audit_entries.append(
                StatisticalAuditEntry(
                    sheet=audit_context[0],
                    question_code=audit_context[1],
                    question_label=audit_context[2],
                    row_label=row_label,
                    comparison="Pairwise",
                    group_a=_column_title(current_position, column),
                    group_b=_column_title(position, other),
                    result=result,
                    reason="Пустая группа." if result is None else None,
                )
            )
        if result is not None and result.significant and result.direction in {"higher", "lower"}:
            findings.append(
                (result.direction, f"{_excel_column_name(position + 1)} — {other['label']}")
            )
    return _format_pairwise_note(findings)


def _proportion_test(
    outcome: pd.Series,
    total_mask: pd.Series,
    column: dict[str, Any],
    eligible_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> StatisticalTestResult | None:
    if not column.get("compare_to_total"):
        return None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    try:
        weights = settings["weights"]
        if weights is not None:
            subgroup_mask = total_mask & column["mask"] & eligible_mask
            rest_mask = total_mask & ~column["mask"] & eligible_mask
            return weighted_proportion_z_test(
                outcome[subgroup_mask],
                weights[subgroup_mask],
                outcome[rest_mask],
                weights[rest_mask],
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        return subgroup_vs_rest_z_test(
            outcome.fillna(False),
            total_mask,
            column["mask"],
            eligible_mask=eligible_mask,
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )
    except ValueError:
        return None


def _mean_test(
    series: pd.Series,
    total_mask: pd.Series,
    column: dict[str, Any],
    base_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> StatisticalTestResult | None:
    if not column.get("compare_to_total"):
        return None
    subgroup_mask = total_mask & column["mask"] & base_mask
    rest_mask = total_mask & ~column["mask"] & base_mask
    subgroup = pd.to_numeric(series[subgroup_mask], errors="coerce").dropna()
    rest = pd.to_numeric(series[rest_mask], errors="coerce").dropna()
    if subgroup.empty or rest.empty:
        return None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    weights = settings["weights"]
    if weights is not None:
        return weighted_welch_t_test(
            subgroup,
            weights.loc[subgroup.index],
            rest,
            weights.loc[rest.index],
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )
    return welch_t_test(
        subgroup,
        rest,
        confidence_level=settings["confidence_level"],
        comparisons=comparisons,
        minimum_base=settings["minimum_base"],
    )


def _comparison_count(column: dict[str, Any], columns: list[dict[str, Any]]) -> int:
    block_columns = [
        item
        for item in columns
        if item.get("block_index") == column.get("block_index")
    ]
    total_comparisons = len(block_columns) if column.get("compare_to_total") else 0
    pairwise_comparisons = (
        len(block_columns) * (len(block_columns) - 1) // 2
        if column.get("compare_pairwise")
        else 0
    )
    wave_columns = [item for item in block_columns if item.get("wave_value") is not None]
    peer_groups = {item.get("wave_peer_key") for item in wave_columns}
    wave_comparisons = (
        max(0, len(wave_columns) - len(peer_groups))
        if column.get("wave_comparison", "none") != "none"
        else 0
    )
    return max(1, total_comparisons + pairwise_comparisons + wave_comparisons)


def _wave_target(
    column: dict[str, Any],
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    mode = settings.get("wave_comparison", "none")
    if mode == "none" or column.get("wave_value") is None:
        return None
    peers = [
        item
        for item in columns
        if item.get("block_index") == column.get("block_index")
        and item.get("wave_peer_key") == column.get("wave_peer_key")
        and item.get("wave_value") is not None
    ]
    if mode == "previous":
        position = next((index for index, item in enumerate(peers) if item is column), -1)
        return peers[position - 1] if position > 0 else None
    control = settings.get("wave_control_value")
    return next(
        (
            item
            for item in peers
            if item is not column and _values_equal(item.get("wave_value"), control)
        ),
        None,
    )


def _wave_proportion_test(
    outcome: pd.Series,
    column: dict[str, Any],
    eligible_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> tuple[dict[str, Any] | None, StatisticalTestResult | None]:
    target = _wave_target(column, columns, settings)
    if target is None:
        return None, None
    current_mask = column["mask"] & eligible_mask
    target_mask = target["mask"] & eligible_mask
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    selected = outcome.fillna(False).astype(bool)
    weights = settings["weights"]
    try:
        if weights is not None:
            result = weighted_proportion_z_test(
                selected[current_mask],
                weights[current_mask],
                selected[target_mask],
                weights[target_mask],
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        else:
            result = proportion_z_test(
                int((selected & current_mask).sum()),
                int(current_mask.sum()),
                int((selected & target_mask).sum()),
                int(target_mask.sum()),
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
    except ValueError:
        result = None
    return target, result


def _wave_mean_test(
    series: pd.Series,
    column: dict[str, Any],
    base_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> tuple[dict[str, Any] | None, StatisticalTestResult | None]:
    target = _wave_target(column, columns, settings)
    if target is None:
        return None, None
    current = pd.to_numeric(series[column["mask"] & base_mask], errors="coerce").dropna()
    previous = pd.to_numeric(series[target["mask"] & base_mask], errors="coerce").dropna()
    if current.empty or previous.empty:
        return target, None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    weights = settings["weights"]
    if weights is not None:
        result = weighted_welch_t_test(
            current,
            weights.loc[current.index],
            previous,
            weights.loc[previous.index],
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )
    else:
        result = welch_t_test(
            current,
            previous,
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )
    return target, result


def _record_wave_comparison(
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
    column: dict[str, Any],
    columns: list[dict[str, Any]],
    target: dict[str, Any] | None,
    result: StatisticalTestResult | None,
) -> None:
    if target is None:
        return
    audit_entries.append(
        StatisticalAuditEntry(
            sheet=audit_context[0],
            question_code=audit_context[1],
            question_label=audit_context[2],
            row_label=row_label,
            comparison="Wave",
            group_a=_worksheet_column_title(_column_position(columns, column), column),
            group_b=_worksheet_column_title(_column_position(columns, target), target),
            result=result,
            reason="Пустая сравниваемая волна." if result is None else None,
        )
    )


def _values_equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right) or str(left) == str(right)
    except (TypeError, ValueError):
        return False


def _pairwise_proportion_note(
    outcome: pd.Series,
    column: dict[str, Any],
    eligible_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
) -> str | None:
    if not column.get("compare_pairwise"):
        return None
    selected = outcome.fillna(False).astype(bool)
    current_mask = column["mask"] & eligible_mask
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    findings: list[tuple[str, str]] = []
    for position, other in enumerate(columns):
        if other is column or other.get("block_index") != column.get("block_index"):
            continue
        other_mask = other["mask"] & eligible_mask
        current_position = _column_position(columns, column)
        if not current_mask.any() or not other_mask.any():
            if current_position < position:
                audit_entries.append(
                    StatisticalAuditEntry(
                        sheet=audit_context[0],
                        question_code=audit_context[1],
                        question_label=audit_context[2],
                        row_label=row_label,
                        comparison="Pairwise",
                        group_a=_column_title(current_position, column),
                        group_b=_column_title(position, other),
                        result=None,
                        reason="Пустая группа.",
                    )
                )
            continue
        weights = settings["weights"]
        if weights is not None:
            result = weighted_proportion_z_test(
                selected[current_mask],
                weights[current_mask],
                selected[other_mask],
                weights[other_mask],
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        else:
            result = proportion_z_test(
                int((selected & current_mask).sum()),
                int(current_mask.sum()),
                int((selected & other_mask).sum()),
                int(other_mask.sum()),
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        if current_position < position:
            audit_entries.append(
                StatisticalAuditEntry(
                    sheet=audit_context[0],
                    question_code=audit_context[1],
                    question_label=audit_context[2],
                    row_label=row_label,
                    comparison="Pairwise",
                    group_a=_column_title(current_position, column),
                    group_b=_column_title(position, other),
                    result=result,
                )
            )
        if result.significant and result.direction in {"higher", "lower"}:
            findings.append(
                (result.direction, f"{_excel_column_name(position + 1)} — {other['label']}")
            )
    return _format_pairwise_note(findings)


def _pairwise_mean_note(
    series: pd.Series,
    column: dict[str, Any],
    base_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
) -> str | None:
    if not column.get("compare_pairwise"):
        return None
    current = pd.to_numeric(series[column["mask"] & base_mask], errors="coerce").dropna()
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    findings: list[tuple[str, str]] = []
    for position, other in enumerate(columns):
        if other is column or other.get("block_index") != column.get("block_index"):
            continue
        other_values = pd.to_numeric(
            series[other["mask"] & base_mask], errors="coerce"
        ).dropna()
        current_position = _column_position(columns, column)
        if current.empty or other_values.empty:
            if current_position < position:
                audit_entries.append(
                    StatisticalAuditEntry(
                        sheet=audit_context[0],
                        question_code=audit_context[1],
                        question_label=audit_context[2],
                        row_label=row_label,
                        comparison="Pairwise",
                        group_a=_column_title(current_position, column),
                        group_b=_column_title(position, other),
                        result=None,
                        reason="Пустая группа.",
                    )
                )
            continue
        weights = settings["weights"]
        if weights is not None:
            result = weighted_welch_t_test(
                current,
                weights.loc[current.index],
                other_values,
                weights.loc[other_values.index],
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        else:
            result = welch_t_test(
                current,
                other_values,
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        if current_position < position:
            audit_entries.append(
                StatisticalAuditEntry(
                    sheet=audit_context[0],
                    question_code=audit_context[1],
                    question_label=audit_context[2],
                    row_label=row_label,
                    comparison="Pairwise",
                    group_a=_column_title(current_position, column),
                    group_b=_column_title(position, other),
                    result=result,
                )
            )
        if result.significant and result.direction in {"higher", "lower"}:
            findings.append(
                (result.direction, f"{_excel_column_name(position + 1)} — {other['label']}")
            )
    return _format_pairwise_note(findings)


def _format_pairwise_note(findings: list[tuple[str, str]]) -> str | None:
    lines = []
    higher = [label for direction, label in findings if direction == "higher"]
    lower = [label for direction, label in findings if direction == "lower"]
    if higher:
        lines.append("Значимо выше: " + ", ".join(higher))
    if lower:
        lines.append("Значимо ниже: " + ", ".join(lower))
    return "\n".join(lines) or None


def _record_total_comparison(
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
    column: dict[str, Any],
    columns: list[dict[str, Any]],
    result: StatisticalTestResult | None,
) -> None:
    if not column.get("compare_to_total"):
        return
    position = _column_position(columns, column)
    audit_entries.append(
        StatisticalAuditEntry(
            sheet=audit_context[0],
            question_code=audit_context[1],
            question_label=audit_context[2],
            row_label=row_label,
            comparison="Subgroup/Rest",
            group_a=_column_title(position, column),
            group_b=f"Rest({_excel_column_name(position + 1)}) — Total − {column['label']}",
            result=result,
            reason="Пустая подгруппа или Rest." if result is None else None,
        )
    )


def _column_title(position: int, column: dict[str, Any]) -> str:
    return f"{_excel_column_name(position + 1)} — {column['label']}"


def _worksheet_column_title(position: int, column: dict[str, Any]) -> str:
    return f"{_excel_column_name(position + 2)} — {column['label']}"


def _column_position(columns: list[dict[str, Any]], target: dict[str, Any]) -> int:
    return next(index for index, column in enumerate(columns) if column is target)


def _render_statistics_txt(
    project: dict[str, Any],
    banner: dict[str, Any],
    configuration: dict[str, Any],
    settings: dict[str, Any],
    entries: list[StatisticalAuditEntry],
) -> str:
    report_filter = "не используется"
    report_filter_id = configuration.get("report_filter_id")
    if report_filter_id:
        selected_filter = next(
            (
                item
                for item in configuration.get("filters", [])
                if item["id"] == report_filter_id
            ),
            None,
        )
        report_filter = selected_filter["name"] if selected_filter else str(report_filter_id)
    lines = [
        "СТАТИСТИЧЕСКИЙ АУДИТ ТОПЛАЙНА",
        f"Проект: {project['name']}",
        f"Исходный SAV: {project.get('original_filename', 'source.sav')}",
        f"Дата расчёта: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Баннер: {banner.get('name', 'Total')}",
        f"Общий фильтр: {report_filter}",
        f"Вес: {settings['weight_label'] or 'не используется'}",
        f"Уровень доверия: {_number(settings['confidence_level'] * 100)}%",
        f"Bonferroni: {'включена' if settings['bonferroni'] else 'выключена'}",
        f"Порог малой базы: N < {settings['minimum_base']}",
        "",
        (
            "Примечание: статистическая значимость рассчитана в модели независимых "
            "наблюдений и сама по себе не подтверждает репрезентативность выборки."
        ),
    ]
    if not entries:
        lines.extend(["", "Статистические сравнения не включены."])
        return "\n".join(lines) + "\n"

    current_sheet = None
    current_question = None
    current_row = None
    for entry in entries:
        if entry.sheet != current_sheet:
            lines.extend(["", f"=== {entry.sheet} ==="])
            current_sheet = entry.sheet
            current_question = None
            current_row = None
        question_key = (entry.question_code, entry.question_label)
        if question_key != current_question:
            lines.extend(["", f"[{entry.question_code}] {entry.question_label}"])
            current_question = question_key
            current_row = None
        if entry.row_label != current_row:
            lines.append(f"  Строка: {entry.row_label}")
            current_row = entry.row_label
        lines.extend(_render_audit_entry(entry))
    return "\n".join(lines) + "\n"


def _render_audit_entry(entry: StatisticalAuditEntry) -> list[str]:
    lines = [
        f"    {entry.comparison}: {entry.group_a} vs {entry.group_b}",
    ]
    result = entry.result
    if result is None:
        lines.append(f"      Статус: пропущен. Причина: {entry.reason or 'Тест неприменим.'}")
        return lines
    lines.append(f"      Метод: {result.method}")
    if result.group_bases is not None:
        lines.append(f"      Базы: N1={result.group_bases[0]}; N2={result.group_bases[1]}")
    if result.group_successes is not None:
        lines.append(
            "      Числители: "
            f"n1={_number(result.group_successes[0])}; "
            f"n2={_number(result.group_successes[1])}"
        )
    if result.group_weight_sums is not None:
        lines.append(
            f"      Суммы весов: sum_w1={_number(result.group_weight_sums[0])}; "
            f"sum_w2={_number(result.group_weight_sums[1])}"
        )
    if result.effective_bases is not None:
        lines.append(
            f"      Эффективные базы: n_eff1={_number(result.effective_bases[0])}; "
            f"n_eff2={_number(result.effective_bases[1])}"
        )
    if result.group_estimates is not None:
        if result.method == "z-test":
            estimate_label = "Доли"
        elif "z-test" in result.method:
            estimate_label = "Балансы"
        else:
            estimate_label = "Средние"
        estimates = result.group_estimates
        lines.append(
            f"      {estimate_label}: group1={_number(estimates[0])}; "
            f"group2={_number(estimates[1])}"
        )
    if result.group_variances is not None:
        variances = result.group_variances
        lines.append(
            f"      Дисперсии: var1={_number(variances[0])}; var2={_number(variances[1])}"
        )
        variance_bases = result.effective_bases or result.group_bases
        if variance_bases is not None:
            standard_errors = (
                math.sqrt(variances[0] / variance_bases[0]),
                math.sqrt(variances[1] / variance_bases[1]),
            )
            lines.append(
                f"      Стандартные ошибки: se1={_number(standard_errors[0])}; "
                f"se2={_number(standard_errors[1])}"
            )
    if result.expected_frequencies is not None:
        lines.append(
            "      Ожидаемые частоты 2×2: "
            + "; ".join(_number(value) for value in result.expected_frequencies)
        )
    difference = result.difference * 100 if "z-test" in result.method else result.difference
    difference_unit = " п.п." if "z-test" in result.method else ""
    lines.append(f"      Разница: {_number(difference)}{difference_unit}")
    if result.confidence_interval is not None:
        interval = result.confidence_interval
        if "z-test" in result.method:
            interval = (interval[0] * 100, interval[1] * 100)
        lines.append(
            f"      Доверительный интервал: [{_number(interval[0])}; "
            f"{_number(interval[1])}]"
        )
    if not result.performed:
        lines.append(f"      Статус: пропущен. Причина: {result.reason}")
        lines.append(f"      Скорректированный alpha: {_number(result.alpha)}")
        return lines
    statistic_name = "z" if "z-test" in result.method else "t"
    lines.append(f"      {statistic_name}={_number(result.statistic)}")
    if result.degrees_of_freedom is not None:
        lines.append(f"      df={_number(result.degrees_of_freedom)}")
    lines.append(f"      p-value={_p_value(result.p_value)}")
    lines.append(f"      Скорректированный alpha: {_number(result.alpha)}")
    decision = "значимо" if result.significant else "незначимо"
    lines.append(f"      Решение: {decision}; направление={result.direction}")
    if result.approximate:
        lines.append("      Характер теста: приближённый")
    return lines


def _number(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:.6f}"


def _p_value(value: float | None) -> str:
    if value is None:
        return "—"
    return "<0.000001" if value < 0.000001 else _number(value)


def _result_format(
    formats: dict[str, Any],
    family: str,
    base: int,
    result: StatisticalTestResult | None,
    settings: dict[str, Any],
    wave_result: StatisticalTestResult | None = None,
) -> Any:
    key = family
    if 0 < base < settings["minimum_base"]:
        key = f"{family}_small"
    elif result is not None and result.significant and result.direction in {"higher", "lower"}:
        key = f"{family}_{result.direction}"
    if (
        wave_result is not None
        and wave_result.significant
        and wave_result.direction in {"higher", "lower"}
    ):
        return formats[f"{key}_wave_{wave_result.direction}"]
    return formats[key]


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


def _formats(workbook: Any) -> dict[str, Any]:
    border = {"bottom": 1, "bottom_color": "#E3E7E3"}
    formats = {
        "title": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 16,
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#355B47",
                "align": "left",
                "valign": "vcenter",
            }
        ),
        "banner": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 9,
                "bold": True,
                "bg_color": "#E7ECE8",
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
            }
        ),
        "banner_letter": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 9,
                "bold": True,
                "bg_color": "#D8E3DC",
                "align": "center",
            }
        ),
        "base": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 9,
                "bold": True,
                "bg_color": "#F1F4F2",
                "align": "center",
                "num_format": "#,##0",
            }
        ),
        "base_label": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "bold": True, "bg_color": "#F1F4F2"}
        ),
        "question": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 10,
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#4F7D65",
                "text_wrap": True,
                "valign": "vcenter",
            }
        ),
        "subquestion": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "bold": True, "bg_color": "#EDF3EF", **border}
        ),
        "percent": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "num_format": "0", "align": "right", **border}
        ),
        "mean": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "num_format": "0.0", "align": "right", **border}
        ),
        "contents_header": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 10,
                "bold": True,
                "bg_color": "#E7ECE8",
                "bottom": 1,
                "bottom_color": "#AAB7AF",
            }
        ),
        "link": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "font_color": "#355B47", "underline": True}
        ),
    }
    result_fills = {
        "higher": "#D9EAD3",
        "lower": "#F4CCCC",
        "small": "#D9D9D9",
    }
    for family, num_format in (("percent", "0"), ("mean", "0.0")):
        for result, color in result_fills.items():
            formats[f"{family}_{result}"] = workbook.add_format(
                {
                    "font_name": "Arial",
                    "font_size": 9,
                    "num_format": num_format,
                    "align": "right",
                    "bg_color": color,
                    **border,
                }
            )
        for base_name, fill_color in {"": None, **result_fills}.items():
            base_key = family if not base_name else f"{family}_{base_name}"
            for direction, arrow, font_color in (
                ("higher", "↑", "#548235"),
                ("lower", "↓", "#C00000"),
            ):
                properties = {
                    "font_name": "Arial",
                    "font_size": 9,
                    "font_color": font_color,
                    "num_format": f'{num_format}" {arrow}"',
                    "align": "right",
                    **border,
                }
                if fill_color:
                    properties["bg_color"] = fill_color
                formats[f"{base_key}_wave_{direction}"] = workbook.add_format(properties)
    return formats


def _find_by_id(items: list[dict[str, Any]], identifier: str, label: str) -> dict[str, Any]:
    found = next((item for item in items if item["id"] == identifier), None)
    if found is None:
        raise ReportError(f"{label} не найден.")
    return found


def _excel_column_name(index: int) -> str:
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _ratio(numerator: Any, denominator: int) -> float | None:
    return float(numerator) / denominator if denominator else None


def _report_weights(
    frame: pd.DataFrame,
    variable: str | None,
    calculated_weight_id: str | None,
    configuration: dict[str, Any],
) -> tuple[pd.Series | None, str | None]:
    if variable and calculated_weight_id:
        raise ReportError("Выберите готовый или рассчитанный вес, но не оба сразу.")
    if calculated_weight_id:
        definition = next(
            (
                item
                for item in configuration.get("calculated_weights", [])
                if item["id"] == str(calculated_weight_id)
            ),
            None,
        )
        if definition is None:
            raise ReportError("Рассчитанный вес не найден в проекте.")
        try:
            result = calculate_raking(frame, definition)
        except WeightingError as exc:
            raise ReportError(str(exc)) from exc
        return result.weights, f"{definition['name']} (raking/IPF)"
    if not variable:
        return None, None
    if variable not in frame.columns:
        raise ReportError("Весовая переменная не найдена в SAV.")
    weights = pd.to_numeric(frame[variable], errors="coerce")
    if weights.isna().any():
        raise ReportError("Весовая переменная должна быть числовой и заполненной для всех строк.")
    if not weights.map(math.isfinite).all() or (weights <= 0).any():
        raise ReportError("Все веса должны быть конечными положительными числами.")
    normalized = weights.astype(float) / float(weights.mean())
    return normalized, variable


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
