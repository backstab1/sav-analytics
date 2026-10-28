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

Выгрузка опросной платформы с заголовками «Q6_1. Пятёрочка Доставка» даёт
и имена переменных, а числовые коды без подписей становятся категориями,
как в SAV той же платформы. Подписи кодов и текст вопросов восстанавливает
анкета JSON (`survey_schema`), если её загрузили вместе с таблицей; ею же
дополняется SAV с пробелами в метаданных (`enrich_sav`).

XLSX читается без сторонних библиотек: это zip с XML, и первого листа
достаточно — значения, общие и встроенные строки. Ячейки с форматом даты
или времени становятся текстом «ДД.ММ.ГГГГ ЧЧ:ММ:СС», как даты в SAV
выгрузок.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import pandas as pd
import pyreadstat

from .sav_writing import SavWriteMismatchError, long_text_columns, verify_written_sav
from .survey_schema import SurveySchema, VariableSpec

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
#: «Q6_1. Пятёрочка Доставка» — имя переменной, точка и подпись.
_NAMED_HEADER = re.compile(r"^([A-Za-z][A-Za-z0-9_]{0,63})\.\s+(\S.*)$", re.DOTALL)
#: С какой доли таких заголовков таблица считается выгрузкой платформы.
NAMED_SHARE = 0.8
#: Встроенные форматы Excel для дат и времени (ECMA-376, 18.8.30).
_DATE_FORMATS = {14: "date", 15: "date", 16: "date", 17: "date", 22: "datetime"}
_TIME_FORMATS = {18, 19, 20, 21, 45, 46, 47}


class TabularImportError(ValueError):
    pass


def is_tabular(filename: str) -> bool:
    return Path(filename).suffix.lower() in TABULAR_EXTENSIONS


def convert_to_sav(source: Path, target: Path, schema: SurveySchema | None = None) -> int:
    """Таблица → SAV; с анкетой JSON подписи и коды берутся из неё.

    Возвращает, сколько столбцов нашлось в анкете.
    """
    frame = read_table(source)
    if frame.empty or not len(frame.columns):
        raise TabularImportError("В таблице нет строк с данными.")
    names, labels = _variable_names(list(frame.columns))
    frame.columns = names
    value_labels: dict[str, dict[float, str]] = {}
    measure: dict[str, str] = {}
    matched = 0
    for position, name in enumerate(names):
        text = frame[name].astype("string").str.strip()
        text = text.mask(text == "")
        spec = schema.lookup(name) if schema is not None else None
        if spec is not None:
            matched += 1
            labels[position] = spec.sav_label
            if _apply_spec(frame, name, text, spec, value_labels, measure):
                continue
        filled = text.dropna()
        numbers = pd.to_numeric(filled.str.replace(",", ".", regex=False), errors="coerce")
        distinct = list(pd.unique(filled))
        # Код, а не количество: значений немного, и каждое в среднем
        # повторяется — возраст в четырёх строках кодом не становится.
        coded = len(distinct) <= MAX_CATEGORIES and len(filled) >= 2 * len(distinct)
        if numbers.notna().all():
            frame[name] = _numbers(text)
            integers = bool((numbers % 1 == 0).all())
            # Выгрузки платформ отдают ответы кодами без подписей: такой
            # столбец — категории, как в SAV той же платформы.
            measure[name] = "nominal" if coded and integers else "scale"
            continue
        # Один и тот же текст во всех строках (название панели, «unknown») —
        # служебное поле, а не вариант ответа.
        if coded and len(distinct) > 1:
            codes = {value: index for index, value in enumerate(distinct, start=1)}
            frame[name] = text.map(codes).astype("float64")
            value_labels[name] = {index: str(value) for value, index in codes.items()}
        else:
            frame[name] = text.fillna("").astype(str)
        measure[name] = "nominal"
    _write(frame, target, dict(zip(names, labels, strict=True)), value_labels, measure)
    return matched


def _apply_spec(
    frame: pd.DataFrame,
    name: str,
    text: pd.Series,
    spec: VariableSpec,
    value_labels: dict[str, dict[float, str]],
    measure: dict[str, str],
) -> bool:
    """Столбец по описанию анкеты; False — описание не подошло к данным.

    Коды берутся как есть, а выгрузка с подписями вместо кодов («Мужчина»)
    переводится в коды анкеты, если каждое значение нашлось среди подписей.
    """
    filled = text.dropna()
    if spec.kind == "text":
        frame[name] = text.fillna("").astype(str)
        measure[name] = "nominal"
        return True
    numbers = pd.to_numeric(filled.str.replace(",", ".", regex=False), errors="coerce")
    if numbers.notna().all():
        frame[name] = _numbers(text)
        measure[name] = spec.measure
        if spec.value_labels:
            value_labels[name] = dict(spec.value_labels)
        return True
    by_label = {label.casefold(): code for code, label in spec.value_labels.items()}
    if by_label and all(value.casefold() in by_label for value in pd.unique(filled)):
        frame[name] = text.map(lambda value: by_label[value.casefold()], na_action="ignore")
        frame[name] = frame[name].astype("float64")
        measure[name] = spec.measure
        value_labels[name] = dict(spec.value_labels)
        return True
    return False


def _numbers(text: pd.Series) -> pd.Series:
    return pd.to_numeric(text.str.replace(",", ".", regex=False), errors="coerce").astype(
        "float64"
    )


def _write(
    frame: pd.DataFrame,
    target: Path,
    labels: dict[str, str],
    value_labels: dict[str, dict[float, str]],
    measure: dict[str, str],
    **options: object,
) -> None:
    try:
        pyreadstat.write_sav(
            frame,
            target,
            column_labels=labels,
            variable_value_labels=value_labels,
            variable_measure=measure,
            **options,
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


def enrich_sav(source: Path, target: Path, schema: SurveySchema) -> int:
    """SAV с подписями из анкеты там, где их нет; число дополненных переменных.

    Метаданные файла сильнее анкеты: заполняются только пустые подписи
    переменных и коды без подписи. Форматы, шкалы и пропуски переписываются
    как были. Наборы multiple-response `write_sav` записать не умеет, поэтому
    SAV с ними не трогается (0): такой файл и так описан полно.
    """
    try:
        frame, meta = pyreadstat.read_sav(
            source, user_missing=True, disable_datetime_conversion=True
        )
    except Exception as exc:  # pyreadstat сообщает об ошибках чтения разными типами
        raise TabularImportError("Файл не удалось прочитать как корректный SPSS SAV.") from exc
    if getattr(meta, "mr_sets", None):
        return 0
    labels = {name: meta.column_names_to_labels.get(name) or "" for name in frame.columns}
    value_labels = {
        name: {float(code): text for code, text in (codes or {}).items()}
        for name, codes in (meta.variable_value_labels or {}).items()
    }
    measure = {
        name: level
        for name, level in (meta.variable_measure or {}).items()
        if level in {"nominal", "ordinal", "scale"}
    }
    enriched = 0
    for name in frame.columns:
        spec = schema.lookup(name)
        if spec is None:
            continue
        changed = False
        if not labels[name].strip() or labels[name].strip() == name:
            labels[name] = spec.sav_label
            changed = True
        if spec.value_labels and pd.api.types.is_numeric_dtype(frame[name]):
            known = value_labels.setdefault(name, {})
            for code, text in spec.value_labels.items():
                if code not in known:
                    known[code] = text
                    changed = True
            if name not in measure or measure[name] == "unknown":
                measure[name] = spec.measure
        enriched += changed
    if not enriched:
        return 0
    missing = dict(meta.missing_ranges or {})
    for name, values in (meta.missing_user_values or {}).items():
        missing.setdefault(name, list(values))
    _write(
        frame,
        target,
        labels,
        {name: codes for name, codes in value_labels.items() if codes},
        measure,
        file_label=meta.file_label or "",
        note=meta.notes or None,
        missing_ranges=missing or None,
        variable_format=dict(meta.original_variable_types or {}),
        variable_display_width=dict(meta.variable_display_width or {}),
    )
    return enriched


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
        styles = _date_styles(archive, names)
        epoch = _epoch(archive, names)
        try:
            root = ElementTree.fromstring(archive.read(sheet_path))
        except ElementTree.ParseError as exc:
            raise TabularImportError("Лист XLSX не удалось прочитать.") from exc
    rows: list[list[str]] = []
    for row in root.iter(f"{{{_SHEET_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.iter(f"{{{_SHEET_NS}}}c"):
            column = _column_index(cell.get("r", ""), len(values))
            values[column] = _cell_text(cell, shared, styles, epoch)
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


def _date_styles(archive: ZipFile, names: set[str]) -> dict[int, str]:
    """Стили ячеек с форматом даты или времени: индекс стиля → вид."""
    if "xl/styles.xml" not in names:
        return {}
    try:
        root = ElementTree.fromstring(archive.read("xl/styles.xml"))
    except ElementTree.ParseError:
        return {}
    custom = {
        int(item.get("numFmtId", "-1")): _format_kind(item.get("formatCode", ""))
        for item in root.iter(f"{{{_SHEET_NS}}}numFmt")
    }
    cell_formats = root.find(f"{{{_SHEET_NS}}}cellXfs")
    if cell_formats is None:
        return {}
    kinds: dict[int, str] = {}
    for index, style in enumerate(cell_formats.iter(f"{{{_SHEET_NS}}}xf")):
        number_format = int(style.get("numFmtId", "0"))
        kind = custom.get(number_format) or _DATE_FORMATS.get(number_format)
        if kind is None and number_format in _TIME_FORMATS:
            kind = "time"
        if kind:
            kinds[index] = kind
    return kinds


def _format_kind(code: str) -> str | None:
    """Вид пользовательского формата: дата, время, дата и время или число."""
    plain = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", code).lower()
    has_date = bool(re.search(r"[dy]", plain))
    has_time = bool(re.search(r"[hs]", plain))
    if has_date and has_time:
        return "datetime"
    if has_date:
        return "date"
    return "time" if has_time else None


def _epoch(archive: ZipFile, names: set[str]) -> datetime:
    """Нулевой день книги: обычно 30.12.1899, у книг с Mac — 1.1.1904."""
    if "xl/workbook.xml" in names:
        try:
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        except ElementTree.ParseError:
            workbook = None
        properties = workbook.find(f"{{{_SHEET_NS}}}workbookPr") if workbook is not None else None
        if properties is not None and properties.get("date1904") in {"1", "true"}:
            return datetime(1904, 1, 1)
    return datetime(1899, 12, 30)


def _date_text(serial: float, kind: str, epoch: datetime) -> str:
    moment = epoch + timedelta(seconds=round(serial * 86400))
    if kind == "date":
        return moment.strftime("%d.%m.%Y")
    if kind == "time":
        return moment.strftime("%H:%M:%S")
    return moment.strftime("%d.%m.%Y %H:%M:%S")


def _cell_text(
    cell: ElementTree.Element,
    shared: list[str],
    styles: dict[int, str] | None = None,
    epoch: datetime = datetime(1899, 12, 30),
) -> str:
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
    style = cell.get("s", "")
    kind = (styles or {}).get(int(style)) if style.isdigit() else None
    if kind is not None:
        # Дата хранится числом дней: «46302,61» стала бы числом, а не датой.
        return _date_text(number, kind, epoch)
    # 1.0 из Excel — это 1: иначе код «1» и «1.0» станут разными категориями.
    return str(int(number)) if number.is_integer() else repr(number)


def _variable_names(headers: list[object]) -> tuple[list[str], list[str]]:
    """Имена и подписи переменных по заголовкам.

    Опросные платформы пишут заголовок как «Q6_1. Пятёрочка Доставка»: имя,
    точка, подпись. Если так устроено большинство заголовков, имя берётся из
    заголовка — иначе «Note. Текст» в обычной таблице потерял бы начало.
    """
    texts = [str(header).strip() for header in headers]
    named = [_NAMED_HEADER.match(text) for text in texts]
    filled = [text for text in texts if text]
    split = bool(filled) and sum(bool(match) for match in named) >= NAMED_SHARE * len(filled)
    names: list[str] = []
    labels: list[str] = []
    used: set[str] = set()
    for position, (text, match) in enumerate(zip(texts, named, strict=True), start=1):
        label = text or f"V{position}"
        candidate = re.sub(r"[^A-Za-z0-9_]", "_", label)
        if split and match:
            candidate, label = match.group(1), match.group(2).strip()
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


__all__ = [
    "TABULAR_EXTENSIONS",
    "TabularImportError",
    "convert_to_sav",
    "enrich_sav",
    "is_tabular",
]
