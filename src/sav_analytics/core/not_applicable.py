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

from .formulas import read_project_frame


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

    frame = read_project_frame(path, project, wanted)
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


# Доля валидной базы, начиная с которой неподписанный код считается скорее
# ответом, чем заглушкой. Порог не отсекает заглушку — Росстат пишет её
# десяткам процентов респондентов, — а только требует подтверждения.
FREQUENT_SHARE = 0.10


@dataclass(frozen=True, slots=True)
class SubstantiveValue:
    """Код, пометка которого может убрать из базы настоящий ответ."""

    value: Any
    label: str | None
    count: int
    share: float

    @property
    def reason(self) -> str:
        return "labelled" if self.label is not None else "frequent"

    def describe(self) -> str:
        who = f"{self.count:,} чел. ({self.share:.0%})".replace(",", " ")
        if self.label is not None:
            return f"код {_format_value(self.value)} «{self.label}» — подписанная категория, {who}"
        return f"код {_format_value(self.value)} без подписи, но у {who} валидной базы"

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "label": self.label,
            "count": self.count,
            "share": self.share,
            "reason": self.reason,
            "description": self.describe(),
        }


@dataclass(frozen=True, slots=True)
class NotApplicableAssessment:
    """Что изменит пометка: валидная база до и после и содержательные коды.

    «До» — база с уже сохранёнными пометками, «после» — с предложенными.
    Содержательными считаются только добавляемые коды: однажды подтверждённая
    пометка второй раз не спрашивается.
    """

    question_code: str
    base_before: int
    base_after: int
    substantive: list[SubstantiveValue] = field(default_factory=list)

    @property
    def requires_confirmation(self) -> bool:
        return bool(self.substantive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_code": self.question_code,
            "base_before": self.base_before,
            "base_after": self.base_after,
            "requires_confirmation": self.requires_confirmation,
            "substantive": [item.to_dict() for item in self.substantive],
        }


class NotApplicableConfirmationRequired(ValueError):
    """Пометка затрагивает содержательный код и не подтверждена явно."""

    def __init__(self, assessments: list[NotApplicableAssessment]) -> None:
        details = "; ".join(
            f"{item.question_code}: {value.describe()}"
            for item in assessments
            for value in item.substantive
        )
        super().__init__(
            "Пометка «не применимо» убирает из базы то, что похоже на ответ — "
            f"{details}. Подтвердите, что это пропуск по ветке анкеты."
        )
        self.assessments = assessments


def assess_not_applicable(
    path: str | Path,
    project: dict[str, Any],
    marks: list[tuple[dict[str, Any], list[Any]]],
) -> list[NotApplicableAssessment]:
    """Оценить пометки сразу для нескольких вопросов одним чтением файла.

    Одна функция служит и предпросмотру в карточке вопроса, и проверке при
    сохранении: показанная аналитику база не может разойтись с той, по которой
    сервер решает, нужно ли подтверждение.
    """
    columns: list[str] = []
    for question, _ in marks:
        for name in question["source_variables"]:
            if name not in columns:
                columns.append(name)
    if not columns:
        return []
    # Как в предпросмотре: объявленные пропуски SPSS уже пустые и в базу не входят.
    frame = read_project_frame(path, project, columns)
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    assessments = []
    for question, values in marks:
        sources = frame[question["source_variables"]]
        raw_valid = int(sources.notna().any(axis=1).sum())
        previous = not_applicable_values(question)
        substantive = []
        for value in values:
            if any(_scalars_equal(value, marked) for marked in previous):
                continue
            count = int(
                pd.concat(
                    [_equals(sources[name], value) for name in sources.columns], axis=1
                ).any(axis=1).sum()
            )
            label = _value_label(question["source_variables"], variables, value)
            share = count / raw_valid if raw_valid else 0.0
            if label is not None or (count and share >= FREQUENT_SHARE):
                substantive.append(SubstantiveValue(value, label, count, share))
        assessments.append(
            NotApplicableAssessment(
                question_code=question["code"],
                base_before=_valid_base(sources, previous),
                base_after=_valid_base(sources, list(values)),
                substantive=substantive,
            )
        )
    return assessments


def _valid_base(sources: pd.DataFrame, values: list[Any]) -> int:
    valid = pd.concat(
        [
            sources[name].notna() & ~is_not_applicable(sources[name], values)
            for name in sources.columns
        ],
        axis=1,
    ).any(axis=1)
    return int(valid.sum())


def _value_label(
    sources: list[str], variables: dict[str, dict[str, Any]], value: Any
) -> str | None:
    for name in sources:
        for item in variables.get(name, {}).get("value_labels", []):
            if _scalars_equal(item["value"], value):
                return str(item["label"])
    return None


def _format_value(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


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
