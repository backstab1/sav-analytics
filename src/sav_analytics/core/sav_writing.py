"""Проверка записанного SAV.

`pyreadstat.write_sav` (readstat) делит строку длиннее 255 байт на части и
называет каждую часть первыми пятью символами имени и номером. У переменной
`Q17.10` вторая часть получает имя `Q17.11` — и если такая переменная уже
есть, файл выходит с лишней колонкой, а при нескольких совпадениях перестаёт
читаться. Обойти это при записи нельзя, поэтому каждый записанный файл
перечитывается: испорченный SAV не должен уходить аналитику молча.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import pyreadstat

LONG_TEXT_BYTES = 255


class SavWriteMismatchError(ValueError):
    pass


def long_text_columns(frame: pd.DataFrame) -> list[str]:
    """Текстовые столбцы, где хоть одно значение длиннее 255 байт в UTF-8."""
    found = []
    for name in frame.columns:
        series = frame[name]
        if series.dtype != object and not pd.api.types.is_string_dtype(series):
            continue
        lengths = series.dropna().map(lambda value: len(str(value).encode("utf-8")))
        if not lengths.empty and int(lengths.max()) > LONG_TEXT_BYTES:
            found.append(name)
    return found


def verify_written_sav(path: str | Path, expected: list[str]) -> None:
    """Убедиться, что файл читается и в нём ровно ожидаемые переменные."""
    try:
        with warnings.catch_warnings():
            # О переименованном дубле pyreadstat предупреждает — здесь это и
            # есть искомая поломка, она проверяется сравнением имён ниже.
            warnings.simplefilter("ignore")
            _, meta = pyreadstat.read_sav(path, metadataonly=True)
    except Exception as exc:  # readstat сообщает об ошибках разными типами
        raise SavWriteMismatchError("Записанный SAV не читается.") from exc
    if list(meta.column_names) != list(expected):
        extra = [name for name in meta.column_names if name not in expected]
        raise SavWriteMismatchError(
            "В записанном SAV появились лишние переменные: " + ", ".join(extra[:10]) + "."
        )
