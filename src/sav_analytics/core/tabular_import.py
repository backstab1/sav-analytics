"""Импорт CSV, TSV и XLSX: таблица превращается в SAV, дальше всё как с SAV.

Опросные сервисы, включая Qualtrics, выгружают CSV. Отдельного пути расчёта
для них нет: файл один раз преобразуется в SAV при загрузке, а оригинал
хранится рядом неизменным. Всё, что следует дальше, — распознавание
структуры, баннеры, книга, аудит — работает с SAV и о CSV не знает.

Что делает преобразование:

- заголовок столбца становится подписью переменной, а имя переменной —
  допустимым для SPSS: латиница без пробелов или `V<номер>`;
- столбец, где каждое непустое значение — число (в том числе с десятичной
  запятой), становится числовым;
- текстовый столбец с немногими значениями становится кодами 1…k с
  подписями в порядке первого появления — так ответы «Мужчина» и «Женщина»
  распознаются одиночным выбором и годятся для баннера;
- остальные текстовые столбцы остаются строками.

XLSX читается без сторонних библиотек: это zip с XML, и первого листа
достаточно — значения, общие и встроенные строки. Даты Excel хранит числами
и приходят числами: формат ячейки не читается.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import pandas as pd
import pyreadstat

from .sav_writing import SavWriteMismatchError, long_text_columns, verify_written_sav

TABULAR_EXTENSIONS = frozenset({".csv", ".tsv", ".xlsx"})
#: Распакованный лист больше этого — вероятнее zip-бомба, чем анкета.
MAX_SHEET_BYTES = 512 * 1024 * 1024
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
#: Больше различных значений у текстового столбца — это уже не варианты
#: ответа, а открытый текст.
MAX_CATEGORIES = 30
_ENCODINGS = ("utf-8-sig", "cp1251")
_VALID_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_RESERVED = {"ALL", "AND", "BY", "EQ", "GE", "GT", "LE", "LT", "NE", "NOT", "OR", "TO", "WITH"}


class TabularImportError(ValueError):
    pass


def is_tabular(filename: str) -> bool:
    return Path(filename).suffix.lower() in TABULAR_EXTENSIONS


def convert_to_sav(source: Path, target: Path) -> None:
    frame = read_table(source)
    if frame.empty or not len(frame.columns):
        raise TabularImportError("В таблице нет строк с данными.")
    names, labels = _variable_names(list(frame.columns))
    frame.columns = names
    value_labels: dict[str, dict[int, str]] = {}
    measure: dict[str, str] = {}
    for name in names:
        text = frame[name].astype("string").str.strip()
        text = text.mask(text == "")
        filled = text.dropna()
        numbers = pd.to_numeric(filled.str.replace(",", ".", regex=False), errors="coerce")
        if numbers.notna().all():
            frame[name] = pd.to_numeric(
                text.str.replace(",", ".", regex=False), errors="coerce"
            ).astype("float64")
            measure[name] = "scale"
            continue
        distinct = list(pd.unique(filled))
        if len(distinct) <= MAX_CATEGORIES:
            codes = {value: position for position, value in enumerate(distinct, start=1)}
            frame[name] = text.map(codes).astype("float64")
            value_labels[name] = {position: str(value) for value, position in codes.items()}
        else:
            frame[name] = text.fillna("").astype(str)
        measure[name] = "nominal"
    try:
        pyreadstat.write_sav(
            frame,
            target,
            column_labels=dict(zip(names, labels, strict=True)),
            variable_value_labels=value_labels,
            variable_measure=measure,
        )
    except Exception as exc:  # pyreadstat сообщает об ошибках записи разными типами
        raise TabularImportError("Таблицу не удалось преобразовать в SAV.") from exc
    try:
        verify_written_sav(target, list(frame.columns))
    except SavWriteMismatchError as exc:
        long_text = long_text_columns(frame)
        raise TabularImportError(
            "Таблицу не удалось преобразовать в SAV без потерь: текстовые столбцы "
            + ", ".join(long_text[:12])
            + " длиннее 255 байт, и при записи их части получают имена соседних "
            "столбцов. Переименуйте эти столбцы или сократите тексты."
        ) from exc


def read_table(source: Path) -> pd.DataFrame:
    if source.suffix.lower() == ".xlsx":
        return _read_xlsx(source)
    separator = "\t" if source.suffix.lower() == ".tsv" else None
    for encoding in _ENCODINGS:
        try:
            with source.open(encoding=encoding, newline="") as stream:
                sample = stream.read(64_000)
            if separator is None:
                try:
                    delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
                except csv.Error:
                    delimiter = ","
            else:
                delimiter = separator
            return pd.read_csv(
                source,
                sep=delimiter,
                dtype=str,
                keep_default_na=False,
                encoding=encoding,
            )
        except UnicodeDecodeError:
            continue
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise TabularImportError("Файл не удалось прочитать как таблицу CSV.") from exc
    raise TabularImportError("Кодировка файла не распознана: сохраните его в UTF-8.")


def _read_xlsx(source: Path) -> pd.DataFrame:
    """Первый лист книги как таблица строк: первая непустая строка — заголовок."""
    try:
        archive = ZipFile(source)
    except BadZipFile as exc:
        raise TabularImportError("Файл XLSX повреждён или это не книга Excel.") from exc
    with archive:
        names = set(archive.namelist())
        sheet_path = _first_sheet_path(archive, names)
        if sheet_path not in names:
            raise TabularImportError("В книге XLSX не найден лист с данными.")
        if archive.getinfo(sheet_path).file_size > MAX_SHEET_BYTES:
            raise TabularImportError("Лист XLSX слишком большой для загрузки.")
        shared = _shared_strings(archive, names)
        try:
            root = ElementTree.fromstring(archive.read(sheet_path))
        except ElementTree.ParseError as exc:
            raise TabularImportError("Лист XLSX не удалось прочитать.") from exc
    rows: list[list[str]] = []
    for row in root.iter(f"{{{_SHEET_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.iter(f"{{{_SHEET_NS}}}c"):
            column = _column_index(cell.get("r", ""), len(values))
            values[column] = _cell_text(cell, shared)
        width = max(values) + 1 if values else 0
        rows.append([values.get(index, "") for index in range(width)])
    rows = [row for row in rows if any(value.strip() for value in row)]
    if not rows:
        raise TabularImportError("В таблице нет строк с данными.")
    header, *body = rows
    width = max(len(row) for row in rows)
    header = header + [""] * (width - len(header))
    body = [row + [""] * (width - len(row)) for row in body]
    return pd.DataFrame(body, columns=header, dtype=str)


def _first_sheet_path(archive: ZipFile, names: set[str]) -> str:
    """Путь первого листа по workbook.xml и его связям; иначе — sheet1.xml."""
    default = "xl/worksheets/sheet1.xml"
    if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
        return default
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relations = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except ElementTree.ParseError:
        return default
    sheet = workbook.find(f"{{{_SHEET_NS}}}sheets/{{{_SHEET_NS}}}sheet")
    if sheet is None:
        return default
    relation_id = sheet.get(f"{{{_REL_NS}}}id")
    for relation in relations.iter(f"{{{_PACKAGE_REL_NS}}}Relationship"):
        if relation.get("Id") == relation_id:
            target = relation.get("Target", "")
            path = PurePosixPath(target.lstrip("/")) if target.startswith("/") else (
                PurePosixPath("xl") / target
            )
            return str(path)
    return default


def _shared_strings(archive: ZipFile, names: set[str]) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(text.text or "" for text in item.iter(f"{{{_SHEET_NS}}}t"))
        for item in root.iter(f"{{{_SHEET_NS}}}si")
    ]


def _column_index(reference: str, fallback: int) -> int:
    letters = re.match(r"[A-Z]+", reference)
    if not letters:
        return fallback
    index = 0
    for letter in letters.group(0):
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        return "".join(text.text or "" for text in cell.iter(f"{{{_SHEET_NS}}}t"))
    value = cell.find(f"{{{_SHEET_NS}}}v")
    raw = "" if value is None or value.text is None else value.text
    if kind == "s":
        return shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else ""
    if kind in {"str", "e", "b"}:
        return raw
    if not raw:
        return ""
    number = float(raw)
    # 1.0 из Excel — это 1: иначе код «1» и «1.0» станут разными категориями.
    return str(int(number)) if number.is_integer() else repr(number)


def _variable_names(headers: list[object]) -> tuple[list[str], list[str]]:
    names: list[str] = []
    labels: list[str] = []
    used: set[str] = set()
    for position, header in enumerate(headers, start=1):
        label = str(header).strip() or f"V{position}"
        candidate = re.sub(r"[^A-Za-z0-9_]", "_", label)
        if not _VALID_NAME.fullmatch(candidate) or candidate.upper() in _RESERVED:
            candidate = f"V{position}"
        base, suffix = candidate[:58], 2
        while candidate.lower() in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate.lower())
        names.append(candidate)
        labels.append(label[:250])
    return names, labels


__all__ = ["TABULAR_EXTENSIONS", "TabularImportError", "convert_to_sav", "is_tabular"]
