from __future__ import annotations

from typing import Any

import pandas as pd

from .not_applicable import applicable_series, excludes


class MultipleResponseError(ValueError):
    """Raised when a multiple-response definition cannot be calculated safely."""


def response_definition(question: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized response-set definition, including legacy projects."""
    question_type = str(question.get("question_type", ""))
    definition = dict(question.get("multiple_response") or {})
    # Представление задаёт тип вопроса. Сохранённое `encoding` могло остаться
    # от прежнего типа, если группу перевели из дихотомии в категории руками.
    if question_type == "multiple_choice_dichotomy":
        definition["encoding"] = "dichotomy"
        definition.setdefault("counted_value", 1)
    elif question_type == "multiple_choice_categorical":
        definition["encoding"] = "categorical"
        definition.pop("counted_value", None)
    return definition


MULTIPLE_TYPES = frozenset({"multiple_choice_dichotomy", "multiple_choice_categorical"})


def is_multiple(question: dict[str, Any]) -> bool:
    """Multiple-response в любом из двух представлений SPSS."""
    return question.get("question_type") in MULTIPLE_TYPES


def answered_mask(frame: pd.DataFrame, question: dict[str, Any]) -> pd.Series:
    """Rows with at least one non-missing value in the response set.

    У категориального представления код «не применимо» в слоте ответом не
    считается — так же, как у одиночного вопроса.
    """
    sources = question.get("source_variables") or []
    if not sources:
        raise MultipleResponseError("Multiple-response не содержит исходных переменных.")
    if response_definition(question).get("encoding") == "categorical":
        return pd.concat(
            [applicable_series(frame[name], question).notna() for name in sources], axis=1
        ).any(axis=1)
    return frame[sources].notna().any(axis=1)


def selected_mask(
    frame: pd.DataFrame, question: dict[str, Any], key: Any
) -> pd.Series:
    """Выбравшие вариант multiple-response.

    У дихотомии вариант — исходная переменная, выбран он, когда в ней записан
    код выбранного ответа. У категориального представления вариант — код
    ответа, а исходные переменные — «слоты», в каждый из которых записан один
    выбранный код; выбран вариант, когда код стоит хотя бы в одном слоте.
    Коды «не применимо» вопроса выбором не считаются.
    """
    definition = response_definition(question)
    sources = question.get("source_variables") or []
    if definition.get("encoding") == "categorical":
        if not sources:
            raise MultipleResponseError("Multiple-response не содержит исходных переменных.")
        if excludes(question, key):
            return pd.Series(False, index=frame.index)
        chosen = pd.Series(False, index=frame.index)
        for name in sources:
            chosen |= _code_equals(frame[name], key)
        return chosen
    if key not in sources:
        raise MultipleResponseError(f"Вариант {key} не входит в multiple-response.")
    counted_value = definition.get("counted_value")
    if counted_value is None:
        raise MultipleResponseError(
            "В metadata multiple-response не задан код выбранного ответа."
        )
    series = frame[key]
    direct = series.eq(counted_value).fillna(False)
    textual = series.notna() & series.astype("string").eq(str(counted_value))
    return (direct | textual).astype(bool)


def response_options(
    question: dict[str, Any],
    variables: dict[str, dict[str, Any]],
    frame: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Варианты ответа multiple-response: ключ для `selected_mask` и подпись.

    Одна точка правды для книги, живой таблицы, превью, NET и баннера: каждый
    из них перебирает варианты этим списком, а не исходные переменные, иначе
    категориальное представление пришлось бы поддерживать в каждом отдельно.

    Категориальные варианты — подписанные коды слотов по возрастанию, затем
    неподписанные коды, встретившиеся в данных (если передан массив). Коды
    «не применимо» вариантами не становятся.
    """
    sources = question.get("source_variables") or []
    if response_definition(question).get("encoding") != "categorical":
        return [
            {"key": name, "label": (variables.get(name) or {}).get("label") or name}
            for name in sources
        ]
    labels: dict[Any, str] = {}
    for name in sources:
        for item in (variables.get(name) or {}).get("value_labels") or []:
            code = _normal_code(item["value"])
            labels.setdefault(code, str(item["label"]))
    if frame is not None:
        for name in sources:
            if name not in frame:
                continue
            for value in frame[name].dropna().unique():
                labels.setdefault(_normal_code(value), str(_normal_code(value)))
    codes = [code for code in labels if not excludes(question, code)]
    codes.sort(key=_code_order)
    return [{"key": code, "label": labels[code]} for code in codes]


def _normal_code(value: Any) -> Any:
    """Код как в подписях SPSS: 3.0 из массива и 3 из подписей — один код."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return int(number) if number.is_integer() else number


def _code_order(code: Any) -> tuple[int, Any]:
    return (0, code) if isinstance(code, int | float) else (1, str(code))


def _code_equals(series: pd.Series, code: Any) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series.dtype):
        try:
            return series.eq(float(code)).fillna(False).astype(bool)
        except (TypeError, ValueError):
            return pd.Series(False, index=series.index)
    return (series.notna() & series.astype("string").eq(str(code))).fillna(False).astype(bool)
