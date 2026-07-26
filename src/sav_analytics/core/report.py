from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat
import xlsxwriter

from .banner import build_banner_columns
from .filtering import evaluate_filter_frame


class ReportError(ValueError):
    pass


def build_topline_xlsx(path: str | Path, project: dict[str, Any]) -> bytes:
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
    if banners:
        columns = build_banner_columns(frame, banners[0], project)
    else:
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
        valid_denominator=True,
    )
    _write_contents(contents, project, questions, main_rows, filter_rows, formats)
    workbook.close()
    return output.getvalue()


def _write_topline(
    sheet: Any,
    frame: pd.DataFrame,
    project: dict[str, Any],
    questions: list[dict[str, Any]],
    variables: dict[str, dict[str, Any]],
    filters: dict[str, dict[str, Any]],
    columns: list[dict[str, Any]],
    formats: dict[str, Any],
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
                formats["percent"],
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
                valid_denominator,
            )
            row = _write_numeric_metric(
                sheet, row, "Среднее", frame[name], columns, base_mask, formats["mean"]
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
                sheet, row, label, series, columns, base_mask, formats["mean"], metric
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
        valid_denominator,
    )
    if question_type == "scale":
        row = _write_numeric_metric(
            sheet, row, "Среднее", series, columns, base_mask, formats["mean"]
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
                formats["percent"],
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
            formats["percent"],
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
    cell_format: Any,
) -> int:
    sheet.write(row, 0, label, cell_format)
    for index, column in enumerate(columns, start=1):
        mask = column["mask"] & base_mask
        if valid_mask is not None:
            mask &= valid_mask
        value = calculator(mask)
        if value is None:
            sheet.write_blank(row, index, None, cell_format)
        else:
            sheet.write_number(row, index, value * 100, cell_format)
    return row + 1


def _write_numeric_metric(
    sheet: Any,
    row: int,
    label: str,
    series: pd.Series,
    columns: list[dict[str, Any]],
    base_mask: pd.Series,
    cell_format: Any,
    metric: str = "mean",
) -> int:
    sheet.write(row, 0, label, cell_format)
    for index, column in enumerate(columns, start=1):
        numeric = pd.to_numeric(series[column["mask"] & base_mask], errors="coerce").dropna()
        value: float | None
        if numeric.empty:
            value = None
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
        if value is None or not math.isfinite(value):
            sheet.write_blank(row, index, None, cell_format)
        else:
            sheet.write_number(row, index, value, cell_format)
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


def _formats(workbook: Any) -> dict[str, Any]:
    border = {"bottom": 1, "bottom_color": "#E3E7E3"}
    return {
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


def _equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right) or str(left) == str(right)
    except (TypeError, ValueError):
        return False
