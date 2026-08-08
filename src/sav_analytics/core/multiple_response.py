from __future__ import annotations

from typing import Any

import pandas as pd


class MultipleResponseError(ValueError):
    """Raised when a multiple-response definition cannot be calculated safely."""


def response_definition(question: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized response-set definition, including legacy projects."""
    question_type = str(question.get("question_type", ""))
    definition = dict(question.get("multiple_response") or {})
    if question_type == "multiple_choice_dichotomy":
        definition.setdefault("encoding", "dichotomy")
        definition.setdefault("counted_value", 1)
    elif question_type == "multiple_choice_categorical":
        definition.setdefault("encoding", "categorical")
    return definition


def answered_mask(frame: pd.DataFrame, question: dict[str, Any]) -> pd.Series:
    """Rows with at least one non-missing value in the response set."""
    sources = question.get("source_variables") or []
    if not sources:
        raise MultipleResponseError("Multiple-response не содержит исходных переменных.")
    return frame[sources].notna().any(axis=1)


def selected_mask(
    frame: pd.DataFrame, question: dict[str, Any], source_variable: str
) -> pd.Series:
    """Rows where one dichotomy item equals the SPSS counted value."""
    definition = response_definition(question)
    if definition.get("encoding") != "dichotomy":
        raise MultipleResponseError(
            "Категориальное представление multiple-response пока не поддерживается."
        )
    if source_variable not in (question.get("source_variables") or []):
        raise MultipleResponseError(
            f"Вариант {source_variable} не входит в multiple-response."
        )
    counted_value = definition.get("counted_value")
    if counted_value is None:
        raise MultipleResponseError(
            "В metadata multiple-response не задан код выбранного ответа."
        )
    series = frame[source_variable]
    direct = series.eq(counted_value).fillna(False)
    textual = series.notna() & series.astype("string").eq(str(counted_value))
    return (direct | textual).astype(bool)

