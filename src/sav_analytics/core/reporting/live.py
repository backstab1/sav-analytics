"""Живая таблица: те же строки, что пишет книга, без файла Excel.

Экран «Таблицы» не получает своих формул. Таблица собирается функцией
:func:`_write_topline` — той же, что пишет лист книги, — только вместо листа
xlsxwriter ей передаётся лист, который запоминает записанное: значение ячейки,
её формат и примечание. Цвет значимости, стрелка волны, серая малая база и
число знаков читаются из того же формата, что уходит в Excel. Поэтому число
на экране и число в книге совпадают по построению, а не по сверке, и
расходиться им негде (роадмап, PQ.3).

Раскладка экрана — вопросы строк, разрез колонок, фильтр — подставляется в
копию проекта. Сохранённая конфигурация не меняется.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..banner import BannerError, banner_columns
from ..filtering import FilterError, filter_columns
from ..report_settings import resolved_report_settings
from .data import prepare_report_data
from .excel_layout import _banner_blocks, _excel_column_name, _write_topline
from .models import ReportError, StatisticalAuditEntry
from .styles import DOWN, FAINT, NEGATIVE, POSITIVE, UP, _formats

#: Типы, которые умеет раскладывать лист книги. Открытый текст и технические
#: переменные в отчёт не входят, и таблица их тоже не строит.
LIVE_QUESTION_TYPES = frozenset(
    {"single_choice", "scale", "numeric", "multiple_choice_dichotomy", "matrix"}
)

_BASE_FORMAT = "#,##0"
_LETTER = re.compile(r"(?:^|, )([A-Z]+) — ")


class _Workbook:
    """Книга, которой нечего записывать: формат остаётся словарём свойств."""

    def add_format(self, properties: dict[str, Any]) -> dict[str, Any]:
        return dict(properties)


class _RecordingSheet:
    """Лист, запоминающий ячейки вместо записи в файл."""

    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], tuple[Any, dict[str, Any]]] = {}
        self.notes: dict[tuple[int, int], str] = {}

    def write(self, row: int, col: int, value: Any, fmt: Any = None, *_: Any) -> int:
        self.cells[(row, col)] = (value, fmt or {})
        return 0

    write_number = write
    write_string = write

    def write_blank(self, row: int, col: int, _blank: Any, fmt: Any = None) -> int:
        self.cells[(row, col)] = (None, fmt or {})
        return 0

    def merge_range(
        self, first_row: int, first_col: int, _last_row: int, _last_col: int,
        value: Any, fmt: Any = None,
    ) -> int:
        self.cells[(first_row, first_col)] = (value, fmt or {})
        return 0

    def write_comment(self, row: int, col: int, text: str, _options: Any = None) -> int:
        self.notes[(row, col)] = text
        return 0

    def __getattr__(self, _name: str) -> Any:
        # Высоты строк, закрепление, группировка, гистограмма — оформление
        # файла, у экрана его нет.
        return _ignore


def _ignore(*_args: Any, **_kwargs: Any) -> int:
    return 0


def build_live_table(
    path: str | Path,
    project: dict[str, Any],
    *,
    questions: list[str],
    banner_id: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
    filter_id: str | None = None,
    sheet: str = "main",
) -> dict[str, Any]:
    """Посчитать таблицу для экрана.

    `sheet="main"` — доли от полной базы, как `topline_main`; `"filter"` —
    от валидной базы вопроса, как `topline_filter`.
    """
    live = _live_project(project, questions, banner_id, blocks, filter_id)
    data = prepare_report_data(path, live, columns=_needed_columns(live))
    recording = _RecordingSheet()
    valid = sheet == "filter"
    chosen = data.filter_questions if valid else data.questions
    entries: list[StatisticalAuditEntry] = []
    positions = _write_topline(
        recording,
        data,
        live,
        chosen,
        _formats(_Workbook(), data.statistical_settings),
        entries,
        "topline_filter" if valid else "topline_main",
        valid_denominator=valid,
    )
    settings = resolved_report_settings(data.configuration, data.active_banner)
    header_rows = 6 if data.statistical_settings["weights"] is not None else 5
    return {
        "sheet": sheet,
        "settings": {
            "confidence_level": settings["confidence_level"],
            "bonferroni": settings["bonferroni"],
            "minimum_base": settings["minimum_base"],
            "compare_to_total": settings["compare_to_total"],
            "compare_target": settings["compare_target"],
            "compare_pairwise": settings["compare_pairwise"],
            "weight": data.statistical_settings["weight_label"],
        },
        "columns": _columns(recording, data, header_rows),
        "blocks": [
            {"label": label, "first": start - 1, "last": end - 1}
            for start, end, label in _banner_blocks(data.columns)
            if label
        ],
        "empty_columns": data.empty_columns,
        "questions": _questions(recording, chosen, positions, len(data.columns)),
        "tests": len(entries),
    }


def _needed_columns(live: dict[str, Any]) -> list[str] | None:
    """Столбцы SAV, нужные этой раскладке, или None — если нужны все.

    Читать весь массив на каждый пересчёт незачем: таблице нужны вопросы
    строк, переменные разреза, фильтров и веса. Список собирают сами модули,
    чтобы имена столбцов не приходилось угадывать снаружи.
    """
    configuration = live["configuration"]
    settings = configuration.get("report_settings") or {}
    if settings.get("calculated_weight_id"):
        # Raking считается по целям веса: какие столбцы ему нужны, знает он сам.
        return None
    names: list[str] = []

    def add(name: str) -> None:
        if name not in names:
            names.append(name)

    filters = {item["id"]: item for item in configuration.get("filters", [])}
    rules = [configuration.get("report_filter_id")]
    for question in configuration["questions"]:
        if not question["included_in_report"]:
            continue
        for name in question["source_variables"]:
            add(name)
        rules.append(question.get("base_filter_id"))
    for rule_id in rules:
        definition = filters.get(rule_id) if rule_id else None
        if definition is None:
            continue
        try:
            for name in sorted(filter_columns(definition, live)):
                add(name)
        except FilterError:
            # Правило сломано — пусть об этом скажет расчёт, а не чтение файла.
            return None
    active = next(
        (
            item
            for item in configuration.get("banners", [])
            if str(item.get("id")) == str(configuration.get("report_banner_id"))
        ),
        None,
    )
    if active is not None:
        try:
            for name in sorted(banner_columns(active, live)):
                add(name)
        except BannerError:
            return None
    weight = settings.get("weight_variable")
    if weight:
        add(weight)
    return names


def _live_project(
    project: dict[str, Any],
    questions: list[str],
    banner_id: str | None,
    blocks: list[dict[str, Any]] | None,
    filter_id: str | None,
) -> dict[str, Any]:
    live = copy.deepcopy(project)
    configuration = live["configuration"]
    by_code = {item["code"]: item for item in configuration["questions"]}
    wanted = list(dict.fromkeys(questions))
    missing = [code for code in wanted if code not in by_code]
    if missing:
        raise ReportError("Вопросы не найдены: " + ", ".join(missing) + ".")
    unsupported = [
        code for code in wanted if by_code[code]["question_type"] not in LIVE_QUESTION_TYPES
    ]
    if unsupported:
        raise ReportError(
            "В таблицу нельзя поставить открытые, технические и пока не поддерживаемые "
            "вопросы: " + ", ".join(unsupported) + "."
        )
    for item in configuration["questions"]:
        item["included_in_report"] = item["code"] in wanted
    # Строки идут в порядке раскладки, а не в порядке структуры.
    configuration["questions"] = [by_code[code] for code in wanted] + [
        item for item in configuration["questions"] if item["code"] not in wanted
    ]

    banners = configuration.get("banners", [])
    if blocks:
        temporary = {"id": str(uuid4()), "name": "Разрез таблицы", "blocks": blocks}
        configuration["banners"] = [*banners, temporary]
        configuration["report_banner_id"] = temporary["id"]
    elif banner_id:
        if not any(str(item.get("id")) == str(banner_id) for item in banners):
            raise ReportError("Баннер не найден.")
        configuration["report_banner_id"] = str(banner_id)
    else:
        configuration["report_banner_id"] = None
    configuration["report_filter_id"] = str(filter_id) if filter_id else None

    settings = dict(configuration.get("report_settings") or {})
    # Экран показывает протокол теста по щелчку, поэтому примечание собирается
    # полным. На числа и решение теста эта настройка не влияет.
    settings["show_p_values"] = True
    if settings.get("wave_comparison", "none") != "none" and not _has_wave_column(configuration):
        settings["wave_comparison"] = "none"
        settings["wave_control_value"] = None
    configuration["report_settings"] = settings
    return live


def _has_wave_column(configuration: dict[str, Any]) -> bool:
    active = next(
        (
            item
            for item in configuration.get("banners", [])
            if str(item.get("id")) == str(configuration.get("report_banner_id"))
        ),
        None,
    )
    if active is None:
        return False
    waves = {item["code"] for item in configuration["questions"] if item.get("role") == "wave"}
    return any(
        source.get("kind") == "question" and source.get("ref") in waves
        for block in active.get("blocks", [])
        for source in block.get("sources", [])
    )


def _columns(
    recording: _RecordingSheet, data: Any, header_rows: int
) -> list[dict[str, Any]]:
    minimum = data.statistical_settings["minimum_base"]
    result = []
    for index, column in enumerate(data.columns, start=1):
        base = int(recording.cells[(4, index)][0])
        weighted = recording.cells.get((5, index)) if header_rows == 6 else None
        result.append(
            {
                "letter": _excel_column_name(index),
                "label": column["label"],
                "base": base,
                "weighted_base": int(weighted[0]) if weighted else None,
                "small": 0 < base < minimum,
            }
        )
    return result


def _questions(
    recording: _RecordingSheet,
    questions: list[dict[str, Any]],
    positions: dict[str, int],
    width: int,
) -> list[dict[str, Any]]:
    starts = sorted((row - 1, code) for code, row in positions.items())
    last_row = max((row for row, _ in recording.cells), default=0)
    labels = {item["code"]: item["label"] for item in questions}
    result = []
    for number, (start, code) in enumerate(starts):
        end = starts[number + 1][0] if number + 1 < len(starts) else last_row + 1
        rows = [
            _row(recording, row, width)
            for row in range(start + 1, end)
            if (row, 0) in recording.cells
        ]
        result.append({"code": code, "label": labels.get(code, code), "rows": rows})
    return result


def _row(recording: _RecordingSheet, row: int, width: int) -> dict[str, Any]:
    label, label_format = recording.cells[(row, 0)]
    written = [recording.cells.get((row, col), (None, {})) for col in range(1, width + 1)]
    if all(value is None for value, _ in written):
        kind = "subquestion"
    elif any(fmt.get("num_format") == _BASE_FORMAT for _, fmt in written):
        kind = "base"
    else:
        kind = "value"
    cells = [
        _cell(value, fmt, recording.notes.get((row, col)))
        for col, (value, fmt) in enumerate(written, start=1)
    ]
    if kind == "value":
        _add_index(cells)
    return {
        # Номер строки на листе книги, считая с единицы, — для сверки с Excel.
        "sheet_row": row + 1,
        "label": label,
        "kind": kind,
        "derived": kind == "value" and "bg_color" in label_format,
        "cells": cells,
    }


def _add_index(cells: list[dict[str, Any]]) -> None:
    """Индекс к Total: 100 — уровень всей выборки.

    Считается из тех же двух чисел строки, что уже показаны рядом, и только
    для них: своего расчёта у индекса нет, поэтому и расходиться ему не с чем.
    Строки базы индекса не получают — сравнивать размеры групп бессмысленно.
    """
    total = cells[0].get("value") if cells else None
    if not total:
        return
    for cell in cells:
        if cell.get("value") is not None:
            cell["index"] = cell["value"] / total * 100


def _cell(value: Any, fmt: dict[str, Any], note: str | None) -> dict[str, Any]:
    if value is None or isinstance(value, str):
        return {"value": None}
    num_format = str(fmt.get("num_format", ""))
    decimals = re.search(r"0\.(0+)$", num_format)
    color = fmt.get("font_color")
    higher: list[str] = []
    lower: list[str] = []
    protocol: list[str] = []
    for block in (note or "").split("\n\n"):
        if block.startswith("Значимо выше: ") or block.startswith("Значимо ниже: "):
            for line in block.splitlines():
                target = higher if line.startswith("Значимо выше: ") else lower
                target.extend(_LETTER.findall(line.split(": ", 1)[1]))
        elif block.strip():
            protocol.append(block)
    return {
        "value": value,
        "decimals": len(decimals.group(1)) if decimals else 0,
        "direction": {POSITIVE: "higher", NEGATIVE: "lower"}.get(color),
        "wave": "higher" if num_format.startswith(f'"{UP}')
        else "lower" if num_format.startswith(f'"{DOWN}')
        else None,
        "small": color == FAINT,
        "higher_than": higher,
        "lower_than": lower,
        "protocol": "\n\n".join(protocol) or None,
    }


__all__ = ["LIVE_QUESTION_TYPES", "build_live_table"]
