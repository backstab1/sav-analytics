"""Шаблон целей рассчитанного веса: выгрузка в Excel и чтение обратно.

`requirements.md` §10 разрешает вводить цели вручную или загружать из
фиксированного шаблона приложения. Шаблон строится по тем переменным, что
уже стоят в редакторе, поэтому ключи строк совпадают с ключами редактора, и
загрузка только расставляет проценты — источники и категории она не меняет.
"""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path
from typing import IO, Any

import xlsxwriter

from .tabular_import import TabularImportError, read_table

MAX_TEMPLATE_BYTES = 2 * 1024 * 1024
CELL_KEY = "ячейка"
COLUMNS = ("Ключ", "Переменная", "Категория", "Код", "Цель, %")


class WeightTargetError(ValueError):
    pass


def build_target_template(definition: dict[str, Any]) -> bytes:
    """Книга целей по измерениям редактора: строка на категорию или ячейку."""
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    sheet = workbook.add_worksheet("Цели")
    header = workbook.add_format({"bold": True, "bg_color": "#E8EEEA", "bottom": 1})
    locked = workbook.add_format({"font_color": "#6B7280"})
    percent = workbook.add_format({"num_format": "0.0###", "bg_color": "#FFF8DB"})
    sheet.write_row(0, 0, COLUMNS, header)
    row = 1
    if definition.get("method") == "cells":
        for cell in definition.get("cells", []):
            sheet.write(row, 0, CELL_KEY, locked)
            sheet.write(row, 1, "Ячейка", locked)
            sheet.write(row, 2, " × ".join(cell["categories"]), locked)
            sheet.write_blank(row, 3, None, locked)
            sheet.write_number(row, 4, float(cell.get("percent") or 0), percent)
            row += 1
    else:
        for dimension in definition.get("dimensions", []):
            key = (
                f"recoding:{dimension['recoding_id']}"
                if dimension.get("recoding_id")
                else dimension["variable"]
            )
            for target in dimension.get("targets", []):
                value = target["values"][0] if target.get("values") else ""
                sheet.write(row, 0, key, locked)
                sheet.write(row, 1, dimension.get("label") or dimension["variable"], locked)
                sheet.write(row, 2, target["label"], locked)
                sheet.write(row, 3, str(value), locked)
                sheet.write_number(row, 4, float(target.get("percent") or 0), percent)
                row += 1
    sheet.set_column(0, 0, 18)
    sheet.set_column(1, 2, 32)
    sheet.set_column(3, 4, 10)
    sheet.freeze_panes(1, 0)
    workbook.close()
    return output.getvalue()


def read_target_file(filename: str, stream: IO[bytes]) -> list[dict[str, Any]]:
    """Строки целей из заполненного шаблона: ключ, категория, код и процент."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".xlsx", ".csv", ".tsv"}:
        raise WeightTargetError("Загрузите шаблон целей в XLSX или CSV.")
    content = stream.read(MAX_TEMPLATE_BYTES + 1)
    if len(content) > MAX_TEMPLATE_BYTES:
        raise WeightTargetError("Файл целей больше 2 МБ — это не шаблон целей.")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"targets{suffix}"
        path.write_bytes(content)
        try:
            table = read_table(path)
        except TabularImportError as exc:
            raise WeightTargetError(str(exc)) from exc
    headers = [str(column).strip() for column in table.columns]
    missing = [column for column in ("Ключ", "Категория", "Цель, %") if column not in headers]
    if missing:
        raise WeightTargetError(
            "В файле нет столбцов шаблона: " + ", ".join(f"«{item}»" for item in missing) + "."
        )
    table.columns = headers
    rows = []
    for position, record in enumerate(table.to_dict("records"), start=2):
        key = str(record.get("Ключ", "")).strip()
        if not key:
            continue
        text = str(record.get("Цель, %", "")).strip().replace(",", ".").rstrip("%")
        try:
            value = float(text)
        except ValueError as exc:
            raise WeightTargetError(f"Строка {position}: цель «{text}» — не число.") from exc
        if not 0 <= value <= 100:
            raise WeightTargetError(f"Строка {position}: цель {value:g}% вне 0–100.")
        rows.append(
            {
                "key": key,
                "category": str(record.get("Категория", "")).strip(),
                "code": str(record.get("Код", "")).strip(),
                "percent": value,
            }
        )
    if not rows:
        raise WeightTargetError("В файле нет ни одной строки целей.")
    return rows
