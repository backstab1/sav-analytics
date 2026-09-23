from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from numbers import Number
from typing import Any

import pandas as pd

from ..filtering import evaluate_filter_frame
from ..multiple_response import answered_mask, is_multiple, response_options, selected_mask
from ..not_applicable import applicable_series, excludes
from ..ranking import ranking_items
from ..statistics import (
    CHI_SQUARE,
    OverallTestResult,
    StatisticalTestResult,
    chi_square_test,
    effective_sample_size,
    skipped_overall,
    weighted_welch_anova,
    welch_anova,
)
from .data import ReportData
from .models import ReportError, StatisticalAuditEntry
from .statistics import (
    StatisticsAuditWriter,
    balance_result,
    cell_note,
    column_position,
    find_wave_target,
    mean_test,
    pairwise_balance_entries,
    pairwise_mean_entries,
    pairwise_proportion_entries,
    proportion_test,
    record_total_comparison,
    record_wave_comparison,
    render_audit_entry,
    unweighted_mean_context,
    unweighted_mean_test,
    unweighted_proportion_context,
    unweighted_proportion_test,
    wave_mean_test,
    wave_proportion_test,
)
from .styles import (
    BAR,
    COLUMN_WIDTH,
    COMMENT_BOX,
    COMMENT_BOX_DETAILED,
    DEFAULT_ROW_HEIGHT,
    LABEL_WIDTH,
    OUTLINE_DETAIL,
    QUESTION_HEIGHT,
    ROW_HEIGHT,
    ReportFormats,
    result_format,
)


@dataclass(frozen=True)
class _RowContext:
    """Куда и с какими настройками пишутся строки одного вопроса.

    Собирается один раз на вопрос в :func:`write_topline` и передаётся вниз
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


def write_topline(
    sheet: Any,
    data: ReportData,
    project: dict[str, Any],
    questions: list[dict[str, Any]],
    formats: ReportFormats,
    audit_entries: list[StatisticalAuditEntry],
    sheet_name: str,
    *,
    valid_denominator: bool,
    audit_writer: StatisticsAuditWriter | None = None,
    advance: Callable[[str], None] | None = None,
    charts: list[tuple[dict[str, Any], list[int]]] | None = None,
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

    for start, end, label in banner_blocks(columns):
        if not label:
            continue
        if end > start:
            sheet.merge_range(1, start, 1, end, label.upper(), formats.block())
        else:
            sheet.write(1, start, label.upper(), formats.block())
    weights = statistical_settings["weights"]
    sheet.set_row(1, 14)
    sheet.set_row(2, QUESTION_HEIGHT)
    sheet.set_row(3, 12)
    sheet.set_row(4, 16)
    for index, column in enumerate(columns, start=1):
        separated = index in separators
        sheet.write(2, index, column["label"], formats.column_label(separated=separated))
        sheet.write(3, index, excel_column_name(index), formats.column_letter())
        sheet.write_number(
            4, index, column["base"], formats.base(separated=separated, rule=weights is None)
        )
    sheet.write(4, 0, "База, N", formats.base_label(rule=weights is None))
    header_rows = 5
    if weights is not None:
        # Доли и средние при включённом весе считаются от суммы весов, а не от
        # числа строк. Невзвешенный N остаётся: по нему проверяется порог малой
        # базы и он же нужен, чтобы читатель видел, на скольких людях всё стоит.
        sheet.set_row(5, 16)
        sheet.write(5, 0, "База, взвеш.", formats.base_label())
        for index, column in enumerate(columns, start=1):
            sheet.write_number(
                5,
                index,
                _weighted_base(column["mask"], weights),
                formats.base(separated=index in separators),
            )
        header_rows = 6
    sheet.freeze_panes(header_rows, 1)
    # Кнопка сворачивания стоит на строке вопроса, то есть над её показателями.
    sheet.outline_settings(True, False, False, True)

    row = header_rows
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
        if charts is not None and context.bars:
            charts.append((question, list(context.bars)))
        if audit_writer is not None:
            audit_writer.write_entries(audit_entries[audit_start:])
            del audit_entries[audit_start:]
        if advance is not None:
            advance(f"{sheet_name}: {question['code']}")
        row += 1
    return positions

def banner_blocks(
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
        start for start, _end, _label in banner_blocks(columns) if start > 1
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

def _same(key: Any, value: Any) -> bool:
    """Ключ варианта и значение из NET: у категориального multiple код может
    прийти из JSON строкой или 3.0, а у дихотомии это имя переменной."""
    if key == value:
        return True
    try:
        return float(key) == float(value)
    except (TypeError, ValueError):
        return str(key) == str(value)


def _write_question_rows(
    context: _RowContext,
    row: int,
    frame: pd.DataFrame,
    question: dict[str, Any],
    variables: dict[str, dict[str, Any]],
) -> int:
    question_type = question["question_type"]
    sources = question["source_variables"]
    if question_type == "ranking":
        chosen = question.get("output_metrics") or context.settings.get(
            "ranking_metrics", ("distribution", "mean")
        )
        for item in ranking_items(frame, question, variables):
            item_context = replace(context, audit_context=(
                context.audit_context[0], context.audit_context[1],
                f"{context.audit_context[2]} — {item['label']}",
            ))
            series = item["ranks"]
            row = _write_subquestion(context, row, item["label"])
            row = _write_valid_base_row(context, row, series.notna())
            for rank in range(1, len(sources) + 1) if "distribution" in chosen else ():
                row = _write_metric_row(
                    item_context, row, f"Место {rank}", context.denominator(series.notna()),
                    series.eq(rank), "percent",
                )
            if "mean" in chosen:
                row = _write_numeric_metric(item_context, row, "Средний ранг", series)
        return row
    if is_multiple(question):
        answered = answered_mask(frame, question)
        options = response_options(question, variables, frame)
        keys = [option["key"] for option in options]
        for option in options:
            row = _write_metric_row(
                context,
                row,
                option["label"],
                context.denominator(answered),
                selected_mask(frame, question, option["key"]),
                "percent",
            )
        for net in question.get("nets", []):
            chosen = [key for key in keys if any(_same(key, value) for value in net["values"])]
            if not chosen:
                continue
            union = pd.concat(
                [selected_mask(frame, question, name) for name in chosen], axis=1
            ).any(axis=1)
            row = _write_metric_row(
                context,
                row,
                f"NET: {net['label']}",
                context.denominator(answered),
                union,
                "percent",
                derived=True,
            )
        return row
    if question_type == "matrix":
        for name in sources:
            item = applicable_series(frame[name], question)
            row = _write_subquestion(context, row, variables[name]["label"])
            row = _write_valid_base_row(context, row, item.notna())
            row = _write_scale_rows(context, row, item, variables[name], question)
        return row
    if len(sources) != 1:
        return row
    series = applicable_series(frame[sources[0]], question)
    row = _write_valid_base_row(context, row, series.notna())
    if question_type == "numeric":
        chosen = question.get("output_metrics") or context.settings.get(
            "numeric_metrics", NUMERIC_METRIC_LABELS
        )
        for metric, label in NUMERIC_METRIC_LABELS.items():
            if metric in chosen:
                row = _write_numeric_metric(context, row, label, series, metric)
        return row
    if question_type == "scale":
        special_metric = question.get("special_metric", "none")
        if special_metric in {"nps", "csat"}:
            # NPS и CSAT — показатели вопроса, а не набора вывода: их строки
            # заданы требованиями целиком и от отметок отчёта не зависят.
            row = _write_distribution(context, row, series, variables[sources[0]], question)
            row = _write_nets(context, row, series, question)
            return _write_special_scale_rows(context, row, series, special_metric)
        return _write_scale_rows(context, row, series, variables[sources[0]], question)
    row = _write_distribution(context, row, series, variables[sources[0]], question)
    return _write_nets(context, row, series, question)


def _write_nets(
    context: _RowContext, row: int, series: pd.Series, question: dict[str, Any]
) -> int:
    """NET-группы вопроса: доля тех, чей ответ — одно из объединённых значений.

    База та же, что у строк распределения над ними, и тест тот же тест долей:
    NET отличается от строки ответа только тем, что объединяет несколько кодов.
    """
    for net in question.get("nets", []):
        outcome = pd.concat(
            [_equal_series(series, value) for value in net["values"]], axis=1
        ).any(axis=1)
        row = _write_metric_row(
            context,
            row,
            f"NET: {net['label']}",
            context.denominator(series.notna()),
            outcome,
            "percent",
            derived=True,
        )
    return row


NUMERIC_METRIC_LABELS = {
    "mean": "Среднее",
    "median": "Медиана",
    "min": "Минимум",
    "max": "Максимум",
    "std": "Стандартное отклонение",
    "stderr": "Стандартная ошибка",
}


def _write_scale_rows(
    context: _RowContext,
    row: int,
    series: pd.Series,
    variable: dict[str, Any],
    question: dict[str, Any],
) -> int:
    """Шкала или элемент матрицы — теми показателями, что отмечены в отчёте."""
    # Свой набор вопроса, если задан, иначе набор отчёта.
    chosen = question.get("output_metrics") or context.settings.get(
        "scale_metrics", ("distribution", "mean", "top2", "bottom2")
    )
    special_values = question.get("special_values", [])
    if "distribution" in chosen:
        row = _write_distribution(context, row, series, variable, question)
    # NET-группы — свойство вопроса, а не набора вывода: они выводятся всегда.
    row = _write_nets(context, row, series, question)
    working = _scale_series(series, special_values)
    if "mean" in chosen:
        row = _write_numeric_metric(context, row, "Среднее", working)
    return _write_scale_aggregates(
        context,
        row,
        working,
        variable,
        special_values,
        top="top2" in chosen,
        bottom="bottom2" in chosen,
    )

def _write_cell_note(
    context: _RowContext,
    row: int,
    index: int,
    comparisons: list[StatisticalAuditEntry | None],
    pairwise: list[StatisticalAuditEntry],
) -> None:
    """Примечание к ячейке, если для неё есть что сказать.

    Сравнения с тоталом и волной несут в книге только цвет и стрелку, поэтому
    в примечание они попадают лишь при включённом выводе p-value: иначе
    примечание встало бы на каждую посчитанную ячейку без нового содержания.
    """
    note = cell_note(
        context.settings, [entry for entry in comparisons if entry], pairwise
    )
    if not note:
        return
    detailed = context.settings["show_p_values"] or context.settings.get("note_skip_reasons")
    box = COMMENT_BOX_DETAILED if detailed else COMMENT_BOX
    context.sheet.write_comment(row, index, note, box)


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
    weights = context.settings["weights"]
    if weights is None:
        return row + 1
    # Знаменатель взвешенных долей этого вопроса — тоже сумма весов, а не строк.
    # Без этой строки на листе рядом стояли бы взвешенные проценты и
    # невзвешенная база, из которой они не выводятся.
    row += 1
    context.sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    context.sheet.write(row, 0, "Валидная база, взвеш.", context.formats.derived_label())
    for index, column in enumerate(context.columns, start=1):
        context.sheet.write_number(
            row,
            index,
            _weighted_base(column["mask"] & eligible, weights),
            context.formats.base(separated=context.separated(index)),
        )
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
    *,
    top: bool = True,
    bottom: bool = True,
) -> int:
    """Строки Top-N и Bottom-N под шкалой — те, что отмечены.

    N — настройка отчёта: сколько крайних кодов шкалы входит в агрегат.
    """
    size = int(context.settings.get("scale_box", 2))
    for label, take_highest in ((f"Top-{size}", True), (f"Bottom-{size}", False)):
        if not (top if take_highest else bottom):
            continue
        selected = _scale_aggregate(
            working, variable, special_values, take_highest=take_highest, size=size
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
    return _write_overall_row(
        context, row, "Хи-квадрат, p", _chi_square_runner(context, series, values)
    )


WEIGHTED_OVERALL_REASON = (
    "Данные взвешены: хи-квадрату нужна поправка Rao–Scott; она ещё не "
    "реализована, поэтому тест не выполняется."
)


def _chi_square_runner(
    context: _RowContext, series: pd.Series, values: list[Any]
) -> Callable[[list[dict[str, Any]]], OverallTestResult]:
    eligible = context.base_mask & series.notna()

    def run(members: list[dict[str, Any]]) -> OverallTestResult:
        settings = context.settings
        if settings["weights"] is not None:
            bases = tuple(int((column["mask"] & eligible).sum()) for column in members)
            return skipped_overall(
                CHI_SQUARE, settings["confidence_level"], bases, WEIGHTED_OVERALL_REASON
            )
        table = [
            [
                int((_equal_series(series, value) & eligible & column["mask"]).sum())
                for column in members
            ]
            for value in values
        ]
        return chi_square_test(
            table,
            confidence_level=settings["confidence_level"],
            minimum_base=settings["minimum_base"],
        )

    return run


def _welch_runner(
    context: _RowContext, series: pd.Series
) -> Callable[[list[dict[str, Any]]], OverallTestResult]:
    numeric = pd.to_numeric(series, errors="coerce")

    def run(members: list[dict[str, Any]]) -> OverallTestResult:
        settings = context.settings
        groups = [numeric[column["mask"] & context.base_mask].dropna() for column in members]
        weights = settings["weights"]
        if weights is not None:
            # То же приближение, что у взвешенного Welch t-test (инвариант 4):
            # взвешенные среднее и дисперсия, размер колонки — n_eff.
            return weighted_welch_anova(
                [group.to_numpy() for group in groups],
                [weights[group.index].to_numpy() for group in groups],
                confidence_level=settings["confidence_level"],
                minimum_base=settings["minimum_base"],
            )
        return welch_anova(
            [group.to_numpy() for group in groups],
            confidence_level=settings["confidence_level"],
            minimum_base=settings["minimum_base"],
        )

    return run


def _write_overall_row(
    context: _RowContext,
    row: int,
    label: str,
    run_test: Callable[[list[dict[str, Any]]], OverallTestResult],
) -> int:
    """Строка общего теста: p-value в первой колонке каждого блока баннера.

    Общий тест отвечает, связан ли показатель с блоком целиком, поэтому число
    одно на блок. Полный протокол — в примечании той же ячейки и в
    `statistics.txt`. Выводится только по настройке отчёта.
    """
    if not context.settings.get("overall_tests"):
        return row
    blocks = [
        (start, end, label_text)
        for start, end, label_text in banner_blocks(context.columns)
        if start > 1
    ]
    if not blocks:
        return row
    sheet = context.sheet
    formats = context.formats
    sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    sheet.write(row, 0, label, formats.derived_label())
    for index in range(1, len(context.columns) + 1):
        blank = formats.overall_blank(separated=context.separated(index))
        sheet.write_blank(row, index, None, blank)
    for start, end, block_label in blocks:
        members = context.columns[start - 1 : end]
        result = run_test(members)
        entry = StatisticalAuditEntry(
            sheet=context.audit_context[0],
            question_code=context.audit_context[1],
            question_label=context.audit_context[2],
            row_label=label,
            comparison="Общий тест",
            group_a=f"блок «{block_label}»" if block_label else f"колонки {start}–{end}",
            group_b="",
            result=None,
            overall=result,
        )
        context.audit_entries.append(entry)
        separated = context.separated(start)
        if result.performed and result.p_value is not None:
            sheet.write_number(
                row,
                start,
                result.p_value,
                formats.overall_value(significant=bool(result.significant), separated=separated),
            )
        else:
            sheet.write_string(row, start, "–", formats.absent(separated=separated, derived=True))
        sheet.write_comment(
            row,
            start,
            "\n".join(line.strip() for line in render_audit_entry(entry)),
            COMMENT_BOX_DETAILED,
        )
    return row + 1

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
    eligible_mask = (
        context.base_mask if valid_mask is None else context.base_mask & valid_mask
    )
    if format_family == "percent" and statistical_settings.get("counts_only"):
        # Лист «Счётчики»: та же строка числом ответивших, без тестов и долей.
        return _write_count_row(context, row, label, outcome, eligible_mask, suffix=False)
    sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    sheet.write(row, 0, label, formats.derived_label() if derived else formats.row_label())
    if format_family == "percent" and not derived:
        context.bars.append(row)
    total_mask = columns[0]["mask"]
    pairwise_cache: dict[tuple[int, int], StatisticalTestResult | None] = {}
    weights = statistical_settings["weights"]
    vectorized = (
        unweighted_proportion_context(outcome, eligible_mask, columns)
        if weights is None
        else None
    )
    # Строка сначала считается целиком и только потом пишется: число знаков
    # выбирается для всей строки, а решить это можно лишь после последней
    # колонки. Порядок записей аудита тот же, что при записи по ходу.
    cells: list[_RowCell] = []
    for index, column in enumerate(columns, start=1):
        position = index - 1
        mask = column["mask"] & eligible_mask if vectorized is None else None
        if vectorized is None:
            value = _weighted_ratio(outcome, mask, weights)
            base = int(mask.sum())
            result = proportion_test(
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
            result = unweighted_proportion_test(
                vectorized,
                position,
                column,
                columns,
                statistical_settings,
            )
        total_entry = record_total_comparison(
            audit_entries, audit_context, label, column, columns, result
        )
        wave_target, wave_result = wave_proportion_test(
            outcome, column, eligible_mask, columns, statistical_settings
        )
        wave_entry = record_wave_comparison(
            audit_entries, audit_context, label, column, columns, wave_target, wave_result
        )
        pairwise = pairwise_proportion_entries(
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
        cells.append(
            _RowCell(
                value, base, result, wave_target, wave_result,
                [total_entry, wave_entry], pairwise,
            )
        )
    _write_row_cells(context, row, cells, format_family, pairwise_cache, derived=derived)
    row += 1
    if format_family != "percent":
        return row
    if statistical_settings.get("show_counts"):
        row = _write_count_row(context, row, label, outcome, eligible_mask)
    if statistical_settings.get("row_percents"):
        row = _write_share_row(context, row, label, outcome, eligible_mask, "row")
    if statistical_settings.get("table_percents"):
        row = _write_share_row(context, row, label, outcome, eligible_mask, "table")
    return row


SHARE_LABELS = {"row": "% по строке", "table": "% от общего"}


def _write_share_row(
    context: _RowContext,
    row: int,
    label: str,
    outcome: pd.Series,
    eligible_mask: pd.Series,
    kind: str,
) -> int:
    """Строка «…, % по строке» или «…, % от общего» под долей.

    По строке: какая часть давших этот ответ приходится на колонку, в Total —
    100. От общего: доля тех, кто дал ответ и попал в колонку, от всей базы
    вопроса. База и вес те же, что у доли над строкой. Это описательные доли:
    тестов у них нет, и цветом они не выделяются.
    """
    weights = context.settings["weights"]
    if weights is None:
        weights = pd.Series(1.0, index=outcome.index)
    selected = outcome.fillna(False).astype(bool) & eligible_mask
    total_mask = context.columns[0]["mask"]
    base_mask = selected if kind == "row" else eligible_mask
    denominator = float(weights[base_mask & total_mask].sum())
    context.sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    context.sheet.write(
        row, 0, f"{label}, {SHARE_LABELS[kind]}", context.formats.derived_label()
    )
    for index, column in enumerate(context.columns, start=1):
        separated = context.separated(index)
        if not denominator:
            context.sheet.write_string(
                row, index, "–", context.formats.absent(separated=separated, derived=True)
            )
            continue
        numerator = float(weights[selected & column["mask"]].sum())
        context.sheet.write_number(
            row,
            index,
            numerator / denominator * 100,
            context.formats.value("percent", separated=separated, derived=True),
        )
    return row + 1


def _write_count_row(
    context: _RowContext,
    row: int,
    label: str,
    outcome: pd.Series,
    eligible_mask: pd.Series,
    *,
    suffix: bool = True,
) -> int:
    """Строка «…, N» под долей: сколько человек в колонке дали этот ответ.

    Число невзвешенное, как база в шапке, и считается от той же базы, что доля
    над ним: вместе они показывают, на скольких людях стоит процент.
    """
    selected = outcome.fillna(False).astype(bool) & eligible_mask
    context.sheet.set_row(row, ROW_HEIGHT, None, OUTLINE_DETAIL)
    context.sheet.write(
        row, 0, f"{label}, N" if suffix else label, context.formats.derived_label()
    )
    for index, column in enumerate(context.columns, start=1):
        context.sheet.write_number(
            row,
            index,
            int((selected & column["mask"]).sum()),
            context.formats.base(separated=context.separated(index), rule=False),
        )
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
        unweighted_mean_context(series, base_mask, columns)
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
        total_entry = None
        if metric == "mean":
            result = (
                unweighted_mean_test(
                    mean_context,
                    index - 1,
                    column,
                    columns,
                    statistical_settings,
                )
                if mean_context is not None
                else mean_test(
                    series,
                    columns[0]["mask"],
                    column,
                    base_mask,
                    columns,
                    statistical_settings,
                )
            )
            total_entry = record_total_comparison(
                audit_entries, audit_context, label, column, columns, result
            )
        wave_target = None
        wave_result = None
        wave_entry = None
        if metric == "mean":
            wave_target, wave_result = wave_mean_test(
                series, column, base_mask, columns, statistical_settings
            )
            wave_entry = record_wave_comparison(
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
            cell_format = result_format(
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
            pairwise = pairwise_mean_entries(
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
            _write_cell_note(
                context, row, index, [total_entry, wave_entry], pairwise
            )
    if metric == "mean":
        return _write_overall_row(
            context, row + 1, "Welch ANOVA, p", _welch_runner(context, series)
        )
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
    cells: list[_RowCell] = []
    for column in columns:
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
        total_entry = None
        if column.get("compare_to_total"):
            total_result = balance_result(
                scores,
                current_mask,
                total_mask & ~column["mask"],
                settings,
                columns,
                column,
                method,
            )
            total_entry = record_total_comparison(
                audit_entries, audit_context, label, column, columns, total_result
            )
        wave_target = find_wave_target(column, columns, settings)
        wave_result = None
        wave_entry = None
        if wave_target is not None:
            wave_result = balance_result(
                scores,
                current_mask,
                wave_target["mask"] & eligible_mask,
                settings,
                columns,
                column,
                method,
            )
            wave_entry = record_wave_comparison(
                audit_entries,
                audit_context,
                label,
                column,
                columns,
                wave_target,
                wave_result,
            )
        pairwise = pairwise_balance_entries(
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
        cells.append(
            _RowCell(
                value, int(current_mask.sum()), total_result, wave_target, wave_result,
                [total_entry, wave_entry], pairwise,
            )
        )
    _write_row_cells(context, row, cells, "percent", pairwise_cache, derived=True)
    return row + 1


@dataclass(frozen=True)
class _RowCell:
    """Посчитанная ячейка строки долей, ещё не записанная в лист."""

    value: float | None
    base: int
    result: StatisticalTestResult | None
    wave_target: dict[str, Any] | None
    wave_result: StatisticalTestResult | None
    comparisons: list[StatisticalAuditEntry | None]
    pairwise: list[StatisticalAuditEntry]


def _write_row_cells(
    context: _RowContext,
    row: int,
    cells: list[_RowCell],
    family: str,
    pairwise_cache: dict[tuple[int, int], StatisticalTestResult | None],
    *,
    derived: bool,
) -> None:
    extra_decimal = family == "percent" and _rounding_hides_difference(
        context.columns, cells, pairwise_cache, context.formats.percent_decimals
    )
    for index, cell in enumerate(cells, start=1):
        separated = context.separated(index)
        if cell.value is None:
            context.sheet.write_string(
                row, index, "–", context.formats.absent(separated=separated, derived=derived)
            )
        else:
            cell_format = result_format(
                context.formats,
                family,
                cell.base,
                cell.result,
                context.settings,
                cell.wave_result,
                separated=separated,
                derived=derived,
                extra_decimal=extra_decimal,
            )
            context.sheet.write_number(row, index, cell.value * 100, cell_format)
        _write_cell_note(context, row, index, cell.comparisons, cell.pairwise)


def _rounding_hides_difference(
    columns: list[dict[str, Any]],
    cells: list[_RowCell],
    pairwise_cache: dict[tuple[int, int], StatisticalTestResult | None],
    decimals: int,
) -> bool:
    """Выглядят ли одинаково после округления две значимо различные ячейки строки.

    Пары — те, что читатель сравнивает на листе: две колонки попарного теста,
    колонка и её волна, колонка и Total. Последняя пара берётся и при сравнении
    с остатком: колонки остатка на листе нет, и выделенное цветом число,
    совпадающее с Total, выглядит ошибкой. Решение о значимости принято по
    полной точности; здесь выбирается только число знаков (requirements.md §9.6).
    """
    pairs = [
        pair
        for pair, result in pairwise_cache.items()
        if result is not None and result.significant
    ]
    for position, cell in enumerate(cells):
        if cell.result is not None and cell.result.significant:
            pairs.append((position, 0))
        if cell.wave_target is not None and cell.wave_result is not None:
            if cell.wave_result.significant:
                pairs.append((position, column_position(columns, cell.wave_target)))
    return any(
        _shown_equal(cells[left].value, cells[right].value, decimals)
        for left, right in pairs
        if left != right
    )


def _shown_equal(left: float | None, right: float | None, decimals: int) -> bool:
    """Одинаковы ли доли в процентах так, как их округлит Excel — половина вверх."""
    if left is None or right is None:
        return False
    quantum = Decimal(1).scaleb(-decimals)

    def shown(value: float) -> Decimal:
        return Decimal(repr(value * 100)).quantize(quantum, rounding=ROUND_HALF_UP)

    return shown(left) == shown(right)

LEGEND = (
    "Цвет числа — отличие от тотала: зелёное выше, красное ниже.",
    "▴ ▾ слева от числа — отличие от предыдущей волны.",
    "Попарные сравнения внутри блока баннера — в примечании к ячейке.",
    "Серое число — база меньше минимальной, тест не проводился.",
    "Лишний знак после запятой в строке — без него значимое различие "
    "округлилось бы до одинаковых чисел.",
    "Бледное тире — значения нет.",
)


#: Больше рядов на графике не читается: баннер шире выводится первыми колонками.
CHART_SERIES_LIMIT = 12
CHART_HEIGHT_ROWS = 16


def write_charts(
    workbook: Any,
    sheet: Any,
    charts: list[tuple[dict[str, Any], list[int]]],
    columns: list[dict[str, Any]],
    formats: ReportFormats,
    source_sheet: str = "topline_main",
) -> None:
    """Лист «Графики»: распределение каждого вопроса родным графиком Excel.

    График не хранит своих чисел — ряды ссылаются на ячейки листа
    `topline_main`, поэтому число на графике и в таблице одно и то же число
    книги. Ряд — колонка баннера, категория — строка распределения. У матрицы
    распределений несколько, и график строится на каждое.
    """
    sheet.hide_gridlines(2)
    sheet.set_row(0, 30)
    sheet.write(0, 0, "Графики", formats.title())
    note = f"Доли по колонкам листа {source_sheet}; графики ссылаются на его ячейки."
    if len(columns) > CHART_SERIES_LIMIT:
        note += f" Показаны первые {CHART_SERIES_LIMIT} колонок из {len(columns)}."
    sheet.write(1, 0, note, formats.meta())
    position = 0
    for question, rows in charts:
        runs = _runs(rows)
        for part, (first, last) in enumerate(runs, start=1):
            chart = workbook.add_chart({"type": "bar"})
            for index in range(1, min(len(columns), CHART_SERIES_LIMIT) + 1):
                chart.add_series(
                    {
                        "name": [source_sheet, 2, index],
                        "categories": [source_sheet, first, 0, last, 0],
                        "values": [source_sheet, first, index, last, index],
                        "gap": 60,
                    }
                )
            title = f"{question['code']}  {question['label']}"
            if len(runs) > 1:
                title += f" · часть {part}"
            chart.set_title({"name": title[:250], "name_font": {"size": 10, "bold": True}})
            chart.set_x_axis({"min": 0, "max": 100, "num_format": "0"})
            chart.set_y_axis({"reverse": True})
            chart.set_legend({"position": "bottom"} if len(columns) > 1 else {"none": True})
            chart.set_size({"width": 720, "height": 300})
            sheet.insert_chart(3 + position * CHART_HEIGHT_ROWS, 0, chart)
            position += 1


def write_parameters(
    sheet: Any, pairs: list[tuple[str, str]], formats: ReportFormats
) -> None:
    """Лист «Параметры»: книга сама говорит, из чего и как собрана.

    Высота строк не задаётся: длинные значения — схема сравнения, правило
    фильтра — переносятся, и строка растёт под них при открытии файла.
    """
    sheet.hide_gridlines(2)
    sheet.set_column(0, 0, 26)
    sheet.set_column(1, 1, 100)
    sheet.set_row(0, 30)
    sheet.write(0, 0, "Параметры", formats.title())
    sheet.write(
        1,
        0,
        "Этого достаточно, чтобы воспроизвести расчёт без доступа к проекту.",
        formats.meta(),
    )
    for offset, (label, value) in enumerate(pairs):
        sheet.write(3 + offset, 0, label, formats.parameter_label())
        sheet.write_string(3 + offset, 1, value, formats.parameter_value())


def write_contents(
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

def excel_column_name(index: int) -> str:
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result

def _ratio(numerator: Any, denominator: int) -> float | None:
    return float(numerator) / denominator if denominator else None

def _weighted_base(mask: pd.Series, weights: pd.Series) -> int:
    """Взвешенная база — сумма весов, целым числом.

    Веса нормированы на среднее, поэтому сумма сопоставима с невзвешенным `N`
    и читается как «столько респондентов эта группа представляет». Дробная
    часть смысла не несёт, а в шапке рядом с целым `N` только мешает.
    """
    return int(round(float(weights[mask].sum())))

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
    size: int = 2,
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
    selected_codes = ordered[-size:] if take_highest else ordered[:size]
    if len(selected_codes) == size:
        return series.isin(selected_codes)
    return pd.Series(False, index=series.index)

