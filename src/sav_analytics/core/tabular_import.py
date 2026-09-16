"""Импорт CSV и TSV: таблица превращается в SAV, дальше всё как с SAV.

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
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd
import pyreadstat

TABULAR_EXTENSIONS = frozenset({".csv", ".tsv"})
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
    frame = _read_table(source)
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


def _read_table(source: Path) -> pd.DataFrame:
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
