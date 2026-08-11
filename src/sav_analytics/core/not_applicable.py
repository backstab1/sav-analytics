"""Коды «не применимо»: значения, которыми в SAV помечен пропуск по ветке анкеты.

Не всякий составитель файла объявляет такой пропуск средствами SPSS. Массивы
Росстата, например, пишут в переменную обычный код `0`, у которого просто нет
value label: `ST_TRUD = 0` стоит у тех, кто вообще не выполнял трудовой
деятельности, и вместе с ним заглушка лежит ещё в сорока переменных блока
занятости. Для файла это валидное значение, и без явного указания пользователя
отличить его от осмысленного нуля нельзя.

Помеченный код ведёт себя как пропуск: он исчезает из распределения, на
`topline_main` эти респонденты остаются в базе Total (доли перестают давать
100%, и видно, к какой части населения вопрос относится), а на `topline_filter`
вопрос считается от валидной базы уже без них — ровно как требует
`requirements.md` §7 для вопросов, задававшихся не всем.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat


def not_applicable_values(question: dict[str, Any]) -> list[Any]:
    values = question.get("not_applicable_values")
    return list(values) if values else []


def is_not_applicable(series: pd.Series, values: list[Any]) -> pd.Series:
    """Маска значений, помеченных как «не применимо»."""
    mask = pd.Series(False, index=series.index)
    for value in values:
        mask |= _equals(series, value)
    return mask


def applicable_series(series: pd.Series, question: dict[str, Any]) -> pd.Series:
    """Серия, в которой помеченные коды заменены на пропуск.

    Замена делается один раз при получении серии вопроса, поэтому распределение,
    валидная база, среднее, Top/Bottom и NPS/CSAT получают одинаковую семантику
    без отдельной правки в каждом из них.
    """
    values = not_applicable_values(question)
    if not values:
        return series
    return series.mask(is_not_applicable(series, values))


def excludes(question: dict[str, Any], value: Any) -> bool:
    """Помечен ли конкретный код как «не применимо»."""
    return any(_scalars_equal(value, marked) for marked in not_applicable_values(question))


@dataclass(frozen=True, slots=True)
class NotApplicableCandidate:
    question_code: str
    question_label: str
    variable: str
    value: float
    count: int
    already_marked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_code": self.question_code,
            "question_label": self.question_label,
            "variable": self.variable,
            "value": self.value,
            "count": self.count,
            "already_marked": self.already_marked,
        }


@dataclass(slots=True)
class NotApplicableGroup:
    """Кандидаты, у которых код стоит ровно у одних и тех же респондентов.

    Совпадение до респондента — и есть подпись пропуска по ветке анкеты: одну
    группу вопросов не задавали одним и тем же людям. Поэтому подтверждать
    удобнее группой, а не поштучно.
    """

    respondents: int
    share: float
    candidates: list[NotApplicableCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "respondents": self.respondents,
            "share": self.share,
            "candidates": [item.to_dict() for item in self.candidates],
        }


def suggest_not_applicable_codes(
    path: str | Path, project: dict[str, Any]
) -> list[NotApplicableGroup]:
    """Предложить коды, похожие на пропуск по ветке анкеты.

    Правило намеренно консервативное: предлагается только код без подписи у
    переменной, где подписаны хотя бы две категории, и только если он лежит вне
    диапазона подписанных кодов. Подписанный ноль (дихотомия 0/1, шкала NPS
    0–10) не предлагается никогда; переменная вовсе без подписей — тоже, потому
    что там ноль часов и ноль как заглушка по данным неразличимы.
    """
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    questions = [
        question
        for question in project["configuration"]["questions"]
        if question["question_type"] != "multiple_choice_dichotomy"
    ]
    wanted: list[str] = []
    for question in questions:
        for name in question["source_variables"]:
            if name in variables and name not in wanted and _labelled_codes(variables[name]):
                wanted.append(name)
    if not wanted:
        return []

    frame, _ = pyreadstat.read_sav(
        path,
        usecols=wanted,
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    groups: dict[str, NotApplicableGroup] = {}
    for question in questions:
        for name in question["source_variables"]:
            if name not in frame.columns:
                continue
            codes = _labelled_codes(variables[name])
            if len(codes) < 2:
                continue
            column = frame[name]
            for value in sorted(column.dropna().unique()):
                numeric = float(value)
                if numeric in codes:
                    continue
                if min(codes) <= numeric <= max(codes):
                    # Код внутри диапазона подписей — скорее потерянная подпись,
                    # чем заглушка. Пусть пользователь увидит его строкой.
                    continue
                mask = column.eq(numeric).fillna(False)
                count = int(mask.sum())
                if not count:
                    continue
                digest = hashlib.sha1(mask.to_numpy().tobytes()).hexdigest()[:16]
                group = groups.setdefault(
                    digest,
                    NotApplicableGroup(respondents=count, share=count / len(frame)),
                )
                group.candidates.append(
                    NotApplicableCandidate(
                        question_code=question["code"],
                        question_label=question["label"],
                        variable=name,
                        value=numeric,
                        count=count,
                        already_marked=excludes(question, numeric),
                    )
                )
    return sorted(
        groups.values(),
        key=lambda item: (-len(item.candidates), -item.respondents),
    )


def _labelled_codes(variable: dict[str, Any]) -> set[float]:
    codes: set[float] = set()
    for item in variable.get("value_labels", []):
        try:
            codes.add(float(item["value"]))
        except (TypeError, ValueError):
            continue
    return codes


def _equals(series: pd.Series, expected: Any) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series.dtype):
        try:
            return series.eq(float(expected)).fillna(False)
        except (TypeError, ValueError):
            return pd.Series(False, index=series.index)
    return series.map(lambda item: _scalars_equal(item, expected))


def _scalars_equal(left: Any, right: Any) -> bool:
    try:
        if float(left) == float(right):
            return True
    except (TypeError, ValueError):
        pass
    return str(left) == str(right)
