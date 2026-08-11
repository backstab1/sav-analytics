from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from numbers import Number
from typing import Any

import pandas as pd

from ..filtering import evaluate_filter_frame
from ..multiple_response import answered_mask, selected_mask
from ..not_applicable import applicable_series, excludes
from ..statistics import StatisticalTestResult, effective_sample_size
from .data import ReportData
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
from .styles import (
    BAR,
    COLUMN_WIDTH,
    COMMENT_BOX,
    DEFAULT_ROW_HEIGHT,
    LABEL_WIDTH,
    OUTLINE_DETAIL,
    QUESTION_HEIGHT,
    ROW_HEIGHT,
    ReportFormats,
    _result_format,
)


@dataclass(frozen=True)
class _RowContext:
    """Куда и с какими настройками пишутся строки одного вопроса.

    Собирается один раз на вопрос в :func:`_write_topline` и передаётся вниз
    целиком, вместо того чтобы протаскивать те же восемь значений через
    каждую функцию записи.
    """

    sheet: Any
    columns: list[dict[str, Any]]
    formats: ReportFormats
    settings: dict[str, Any]
    audit_entries: list[StatisticalAuditEntry]
    audit_context: tuple[str, str, str]
    base_mask: pd.Series
    valid_denominator: bool
    separators: frozenset[int]
    #: Строки распределения текущего вопроса — под гистограмму в колонке тотала.
    bars: list[int]

    def denominator(self, valid: pd.Series) -> pd.Series | None:
        """Знаменатель доли: валидная база или None для полной базы."""
        return valid if self.valid_denominator else None

    def separated(self, index: int) -> bool:
        """Начинает ли колонка новый блок баннера."""
        return index in self.separators


def _write_topline(
    sheet: Any,
    data: ReportData,
    project: dict[str, Any],
    questions: list[dict[str, Any]],
    formats: ReportFormats,
    audit_entries: list[StatisticalAuditEntry],
    sheet_name: str,
    *,
    valid_denominator: bool,
    audit_writer: _StatisticsAuditWriter | None = None,
    advance: Callable[[str], None] | None = None,
) -> dict[str, int]:
    frame = data.frame
    variables = data.variables
    filters = data.filters
    columns = data.columns
    statistical_settings = data.statistical_settings
    last_column = len(columns)
    separators = _block_separators(columns)
    sheet.hide_gridlines(2)
    sheet.set_default_row(DEFAULT_ROW_HEIGHT)
    sheet.set_column(0, 0, LABEL_WIDTH)
    sheet.set_column(1, last_column, COLUMN_WIDTH)

    sheet.set_row(0, 30)
    sheet.write(0, 0, "Топлайн", formats.title())
    caption = _caption(project, data)
    if last_column > 1:
        sheet.merge_range(0, 1, 0, last_column, caption, formats.meta())
    else:
        sheet.write(0, 1, caption, formats.meta())

    for start, end, label in _banner_blocks(columns):
        if not label:
            continue
        if end > start:
            sheet.merge_range(1, start, 1, end, label.upper(), formats.block())
        else:
            sheet.write(1, start, label.upper(), formats.block())
    sheet.set_row(1, 14)
    sheet.set_row(2, QUESTION_HEIGHT)
    sheet.set_row(3, 12)
    sheet.set_row(4, 16)
    for index, column in enumerate(columns, start=1):
        separated = index in separators
        sheet.write(2, index, column["label"], formats.column_label(separated=separated))
        sheet.write(3, index, _excel_column_name(index), formats.column_letter())
        sheet.write_number(4, index, column["base"], formats.base(separated=separated))
    sheet.write(4, 0, "База, N", formats.base_label())
    sheet.freeze_panes(5, 1)
    # Кнопка сворачивания стоит на строке вопроса, то есть над её показателями.
    sheet.outline_settings(True, False, False, True)

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
        # Формулировка живёт в закреплённой колонке A и переносится внутри неё:
        # высота строки задана явно, поэтому хвост подписи обрезается, а не
        # растягивает строку.
        sheet.set_row(row, QUESTION_HEIGHT)
        sheet.write(
            row, 0, f"{question['code']}   {question['label']}", formats.question()
        )
        for index in range(1, last_column + 1):
            sheet.write_blank(row, index, None, formats.question_rule())
        row += 1
        context = _RowContext(
            sheet=sheet,
            columns=columns,
            formats=formats,
            settings=statistical_settings,
            audit_entries=audit_entries,
            audit_context=(sheet_name, question["code"], question["label"]),
            base_mask=base_mask,
            valid_denominator=valid_denominator,
            separators=separators,
            bars=[],
        )
        row = _write_question_rows(context, row, frame, question, variables)
        _write_bars(sheet, context.bars)
        if audit_writer is not None:
            audit_writer.write_entries(audit_entries[audit_start:])
            del audit_entries[audit_start:]
        if advance is not None:
            advance(f"{sheet_name}: {question['code']}")
        row += 1
    return positions

def _banner_blocks(
    columns: list[dict[str, Any]],
) -> list[tuple[int, int, str | None]]:
    """Границы блоков баннера как (первая колонка, последняя, подпись).

    Колонка тотала идёт без ``block_index`` и всегда образует свою группу.
    """
    groups: list[list[Any]] = []
    for index, column in enumerate(columns, start=1):
        key = column.get("block_index")
        if groups and key is not None and groups[-1][2] == key:
            groups[-1][1] = index
            continue
        groups.append([index, index, key, column.get("block")])
    return [(start, end, label) for start, end, _key, label in groups]

def _block_separators(columns: list[dict[str, Any]]) -> frozenset[int]:
    return frozenset(
        start for start, _end, _label in _banner_blocks(columns) if start > 1
    )

def _caption(project: dict[str, Any], data: ReportData) -> str:
    settings = data.statistical_settings
    base = f"{data.columns[0]['base']:,}".replace(",", " ")
    weight = settings.get("weight_label") or "без веса"
    alpha = f"{1 - float(settings['confidence_level']):.2f}".replace(".", ",")
    return f"{project['name']}   ·   n = {base}   ·   {weight}   ·   α = {alpha}"

def _write_bars(sheet: Any, rows: list[int]) -> None:
    """Гистограмма в колонке тотала — только по строкам распределения.

    Строки идут группами: у матричного вопроса распределение повторяется для
    каждого подвопроса и разделяется производными строками, поэтому правило
    ставится на каждый непрерывный отрезок отдельно.
    """
    for start, end in _runs(rows):
        sheet.conditional_format(
            start,
            1,
            end,
            1,
            {
                "type": "data_bar",
                "bar_color": BAR,
                "bar_border_color": BAR,
                "bar_solid": True,
                "min_type": "num",
                "min_value": 0,
                "max_type": "num",
                "max_value": 100,
            },
        )

def _runs(rows: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for row in sorted(rows):
        if runs and row == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], row)
        else:
            runs.append((row, row))
    return runs

def _write_question_rows(
    context: _RowContext,
    row: int,
    frame: pd.DataFrame,
    question: dict[str, Any],
    variables: dict[str, dict[str, Any]],
) -> int:
    question_type = question["question_type"]
    sources = question["source_variables"]
    special_values = question.get("special_values", [])
    if question_type == "multiple_choice_dichotomy":
        answered = answered_mask(frame, question)
        for name in sources:
            selected = selected_mask(frame, question, name)
            row = _write_metric_row(
                context,
                row,
                variables[name]["label"],
                context.denominator(answered),
                selected,
                "percent",
            )
        return row
    if question_type == "matrix":
        for name in sources:
            item = applicable_series(frame[name], question)
            working = _scale_series(item, special_values)
            row = _write_subquestion(context, row, variables[name]["label"])
            row = _write_valid_base_row(context, row, item.notna())
            row = _write_distribution(context, row, item, variables[name], question)
            row = _write_numeric_metric(context, row, "Среднее", working)
            row = _write_scale_aggregates(
                context, row, working, variables[name], special_values
            )
        return row
    if len(sources) != 1:
        return row
    series = applicable_series(frame[sources[0]], question)
    row = _write_valid_base_row(context, row, series.notna())
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
            row = _write_numeric_metric(context, row, label, series, metric)
        return row
    row = _write_distribution(context, row, series, variables[sources[0]], question)
    if question_type == "scale":
        special_metric = question.get("special_metric", "none")
        if special_metric in {"nps", "csat"}:
            return _write_special_scale_rows(context, row, series, special_metric)
        working = _scale_series(series, special_values)
        row = _write_numeric_metric(context, row, "Среднее", working)
        row = _write_scale_aggregates(
            context, row, working, variables[sources[0]], special_values
        )
    return row

def _write_valid_base_row(context: _RowContext, row: int, valid: pd.Series) -> int:
    """Строка валидной базы вопроса на `topline_filter`.

    Шапка листа показывает базу колонки баннера — одну на весь лист. На
    `topline_filter` знаменатель у каждого вопроса свой, и без этой строки
    читатель видел бы проценты от одного числа рядом с другим числом в шапке.
    """
    if not context.valid_denominator:
        return row
    eligible = context.base_mask & valid
    context.sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    context.sheet.write(row, 0, "Валидная база, N", context.formats.derived_label())
    for index, column in enumerate(context.columns, start=1):
        base = int((column["mask"] & eligible).sum())
        context.sheet.write_number(row, index, base, context.formats.base(
            separated=context.separated(index)
        ))
    return row + 1


def _write_subquestion(context: _RowContext, row: int, label: str) -> int:
    """Подпись подвопроса матрицы — полосой во всю ширину баннера."""
    subquestion = context.formats.subquestion()
    context.sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    context.sheet.write(row, 0, label, subquestion)
    for index in range(1, len(context.columns) + 1):
        context.sheet.write_blank(row, index, None, subquestion)
    return row + 1

def _write_scale_aggregates(
    context: _RowContext,
    row: int,
    working: pd.Series,
    variable: dict[str, Any],
    special_values: list[Any],
) -> int:
    """Строки Top-2 и Bottom-2 под шкалой."""
    for label, take_highest in (("Top-2", True), ("Bottom-2", False)):
        selected = _scale_aggregate(
            working, variable, special_values, take_highest=take_highest
        )
        row = _write_metric_row(
            context,
            row,
            label,
            context.denominator(working.notna()),
            selected,
            "percent",
            derived=True,
        )
    return row

def _write_special_scale_rows(
    context: _RowContext,
    row: int,
    series: pd.Series,
    metric: str,
) -> int:
    numeric = pd.to_numeric(series, errors="coerce")
    expected = set(range(11)) if metric == "nps" else set(range(1, 6))
    observed = set(numeric.dropna().unique())
    if not observed or not observed <= expected:
        label = "NPS 0–10" if metric == "nps" else "CSAT 1–5"
        raise ReportError(
            f"Вопрос {context.audit_context[1]} не соответствует шкале {label}."
        )
    valid_mask = context.denominator(numeric.notna())
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
    score[numeric.isna()] = math.nan if context.valid_denominator else 0
    for label, selected in groups:
        row = _write_metric_row(context, row, label, valid_mask, selected, "percent")
    row = _write_balance_metric_row(
        context,
        row,
        balance_label,
        score,
        context.base_mask & numeric.notna()
        if context.valid_denominator
        else context.base_mask,
        method,
    )
    if metric == "csat":
        row = _write_metric_row(
            context,
            row,
            "% удовлетворённых",
            valid_mask,
            numeric.between(4, 5),
            "percent",
            derived=True,
        )
    return row

def _write_distribution(
    context: _RowContext,
    row: int,
    series: pd.Series,
    variable: dict[str, Any],
    question: dict[str, Any] | None = None,
) -> int:
    labels = {str(item["value"]): item["label"] for item in variable["value_labels"]}
    values = [item["value"] for item in variable["value_labels"]]
    values.extend(value for value in series.dropna().unique() if str(value) not in labels)
    if question is not None:
        # Серия уже очищена, но подписанный код мог прийти из value labels:
        # помеченный ответ не должен оставаться пустой строкой в отчёте.
        values = [value for value in values if not excludes(question, value)]
    try:
        values = sorted(values, key=float)
    except (TypeError, ValueError):
        pass
    for value in values:
        row = _write_metric_row(
            context,
            row,
            labels.get(str(value), str(value)),
            context.denominator(series.notna()),
            _equal_series(series, value),
            "percent",
        )
    return row

def _write_metric_row(
    context: _RowContext,
    row: int,
    label: str,
    valid_mask: pd.Series | None,
    outcome: pd.Series,
    format_family: str,
    *,
    derived: bool = False,
) -> int:
    sheet = context.sheet
    columns = context.columns
    formats = context.formats
    statistical_settings = context.settings
    audit_entries = context.audit_entries
    audit_context = context.audit_context
    sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    sheet.write(row, 0, label, formats.derived_label() if derived else formats.row_label())
    if format_family == "percent" and not derived:
        context.bars.append(row)
    eligible_mask = (
        context.base_mask if valid_mask is None else context.base_mask & valid_mask
    )
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
        separated = context.separated(index)
        if value is None:
            sheet.write_string(
                row, index, "–", formats.absent(separated=separated, derived=derived)
            )
        else:
            cell_format = _result_format(
                formats,
                format_family,
                base,
                result,
                statistical_settings,
                wave_result,
                separated=separated,
                derived=derived,
            )
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
            sheet.write_comment(row, index, note, COMMENT_BOX)
    return row + 1

def _write_numeric_metric(
    context: _RowContext,
    row: int,
    label: str,
    series: pd.Series,
    metric: str = "mean",
) -> int:
    sheet = context.sheet
    columns = context.columns
    formats = context.formats
    statistical_settings = context.settings
    audit_entries = context.audit_entries
    audit_context = context.audit_context
    base_mask = context.base_mask
    sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    sheet.write(row, 0, label, formats.derived_label())
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
        separated = context.separated(index)
        if value is None or not math.isfinite(value):
            sheet.write_string(
                row, index, "–", formats.absent(separated=separated, derived=True)
            )
        else:
            cell_format = _result_format(
                formats,
                "mean",
                len(numeric),
                result,
                statistical_settings,
                wave_result,
                separated=separated,
                derived=True,
            )
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
    context: _RowContext,
    row: int,
    label: str,
    scores: pd.Series,
    eligible_mask: pd.Series,
    method: str,
) -> int:
    sheet = context.sheet
    columns = context.columns
    formats = context.formats
    settings = context.settings
    audit_entries = context.audit_entries
    audit_context = context.audit_context
    sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    sheet.write(row, 0, label, formats.derived_label())
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
        separated = context.separated(index)
        if value is None:
            sheet.write_string(
                row, index, "–", formats.absent(separated=separated, derived=True)
            )
        else:
            cell_format = _result_format(
                formats,
                "percent",
                int(current_mask.sum()),
                total_result,
                settings,
                wave_result,
                separated=separated,
                derived=True,
            )
            sheet.write_number(row, index, value * 100, cell_format)
        if pairwise_note:
            sheet.write_comment(row, index, pairwise_note, {"author": "sav-analytics"})
    return row + 1

LEGEND = (
    "Цвет числа — отличие от тотала: зелёное выше, красное ниже.",
    "▴ ▾ слева от числа — отличие от предыдущей волны.",
    "Попарные сравнения внутри блока баннера — в примечании к ячейке.",
    "Серое число — база меньше минимальной, тест не проводился.",
    "Бледное тире — значения нет.",
)


def _write_contents(
    sheet: Any,
    project: dict[str, Any],
    questions: list[dict[str, Any]],
    main_rows: dict[str, int],
    filter_rows: dict[str, int],
    formats: ReportFormats,
) -> None:
    sheet.hide_gridlines(2)
    sheet.set_default_row(DEFAULT_ROW_HEIGHT)
    sheet.set_column(0, 0, 10)
    sheet.set_column(1, 1, 62)
    sheet.set_column(2, 2, 20)
    sheet.set_row(0, 30)
    sheet.write(0, 0, "Содержание", formats.title())
    sheet.write(1, 0, project["name"], formats.meta())
    sheet.write_row(3, 0, ["Код", "Название", "Лист"], formats.contents_header())
    row = 4
    for question in questions:
        code = question["code"]
        sheet.set_row(row, ROW_HEIGHT)
        sheet.write_url(
            row,
            0,
            f"internal:'topline_main'!A{main_rows[code]}",
            formats.link(),
            code,
        )
        sheet.write(row, 1, question["label"], formats.contents_label())
        sheet.write(row, 2, "topline_main", formats.contents_sheet())
        row += 1
    if filter_rows:
        row += 1
        sheet.write_row(
            row, 0, ["Код", "Фильтровые вопросы", "Лист"], formats.contents_header()
        )
        row += 1
        by_code = {item["code"]: item for item in questions}
        for code, target_row in filter_rows.items():
            sheet.set_row(row, ROW_HEIGHT)
            sheet.write_url(
                row,
                0,
                f"internal:'topline_filter'!A{target_row}",
                formats.link(),
                code,
            )
            sheet.write(row, 1, by_code[code]["label"], formats.contents_label())
            sheet.write(row, 2, "topline_filter", formats.contents_sheet())
            row += 1
    row += 2
    for line in LEGEND:
        sheet.write(row, 1, line, formats.legend())
        row += 1
    sheet.freeze_panes(4, 0)

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

