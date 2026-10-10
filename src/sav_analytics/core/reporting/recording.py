"""Запись листа книги в память: тот же `write_topline`, без файла.

Лист, который вместо xlsxwriter запоминает записанное: значение ячейки, её
формат словарём и примечание. Из записи собирается таблица — колонки,
вопросы, строки и ячейки с цветом значимости и буквами, — которую
показывает экран «Таблицы» и из которой строится презентация. Своих формул
здесь нет: всё, что известно о ячейке, прочитано из того, что записал лист.
"""

from __future__ import annotations

import re
from typing import Any

from .excel_layout import excel_column_name
from .styles import DOWN, FAINT, NEGATIVE, POSITIVE, UP

BASE_FORMAT = "#,##0"
_LETTER = re.compile(r"(?:^|, )([A-Za-z]+) — ")


class RecordingWorkbook:
    """Книга, которой нечего записывать: формат остаётся словарём свойств."""

    def add_format(self, properties: dict[str, Any]) -> dict[str, Any]:
        return dict(properties)


class RecordingSheet:
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


def table_columns(
    recording: RecordingSheet, data: Any, header_rows: int
) -> list[dict[str, Any]]:
    minimum = data.statistical_settings["minimum_base"]
    result = []
    for index, column in enumerate(data.columns, start=1):
        base = int(recording.cells[(4, index)][0])
        weighted = recording.cells.get((5, index)) if header_rows == 6 else None
        result.append(
            {
                "letter": excel_column_name(index),
                "label": column["label"],
                "base": base,
                "weighted_base": int(weighted[0]) if weighted else None,
                "small": 0 < base < minimum,
            }
        )
    return result


def table_questions(
    recording: RecordingSheet,
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


def _row(recording: RecordingSheet, row: int, width: int) -> dict[str, Any]:
    label, label_format = recording.cells[(row, 0)]
    written = [recording.cells.get((row, col), (None, {})) for col in range(1, width + 1)]
    if all(value is None for value, _ in written):
        kind = "subquestion"
    elif any(fmt.get("num_format") == BASE_FORMAT for _, fmt in written):
        kind = "base"
    else:
        kind = "value"
    cells = [
        _cell(value, fmt, recording.notes.get((row, col)))
        for col, (value, fmt) in enumerate(written, start=1)
    ]
    # Доли по строке и от общего с Total не сравниваются: в Total у первой
    # всегда 100, индекс к нему ничего не говорит.
    if kind == "value" and not str(label).endswith(SHARE_SUFFIXES):
        _add_index(cells)
    return {
        # Номер строки на листе книги, считая с единицы, — для сверки с Excel.
        "sheet_row": row + 1,
        "label": label,
        "kind": kind,
        "derived": kind == "value" and "bg_color" in label_format,
        "cells": cells,
    }


SHARE_SUFFIXES = (", % по строке", ", % от общего")


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
    higher_weak: list[str] = []
    lower_weak: list[str] = []
    protocol: list[str] = []
    for block in (note or "").split("\n\n"):
        if block.startswith("Значимо "):
            for line in block.splitlines():
                caption, _, groups = line.partition(": ")
                # «Значимо выше при 90%» — второй уровень доверия, буквы строчные.
                weak = " при " in caption
                if caption.startswith("Значимо выше"):
                    target = higher_weak if weak else higher
                else:
                    target = lower_weak if weak else lower
                target.extend(_LETTER.findall(groups))
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
        "higher_than_secondary": higher_weak,
        "lower_than_secondary": lower_weak,
        "protocol": "\n\n".join(protocol) or None,
    }
