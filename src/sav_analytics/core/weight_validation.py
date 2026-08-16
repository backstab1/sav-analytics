"""Пригодность готовой весовой переменной.

До этого модуля готовым весом становилась любая строго положительная числовая
колонка: `validate_report_settings` проверял только существование переменной в
SAV, а `_report_weights` — числовой тип, конечность и положительность. Роль
переменной не проверял никто, поэтому `ID` сохранялся весом, проходил preflight
без единого замечания и давал математически валидный отчёт, внешне неотличимый
от правильного.

Проверка сведена в одну функцию намеренно. Роадмап требует, чтобы UI, API и
preflight звали её, а не повторяли условия: три копии условия расходятся на
первой же правке порога, и тогда интерфейс снова разрешит то, что отвергает
сборка.

Двух правил недостаточно по отдельности, поэтому здесь их два.

* **Роль** отсекает `ID` и обычный числовой вопрос до всякой арифметики, но
  только её мало: `infer_role` узнаёт лишь точные имена
  `weight|weights|wgt|ves|вес`, поэтому реальный вес `w_final` роль не получит
  автоматически, а правильно названный, но сломанный вес её не потеряет.
* **Распределение** отсекает сломанный вес независимо от имени. Одного design
  effect тоже мало: у `ID` на 240 респондентов он равен 1,33 — весы растут
  равномерно, и эффективная база почти не страдает. Ловит такой вес доля
  экстремальных значений (10% при пороге 5%).

Пороги ниже — блокирующие, а не предупреждающие: `requirements.md` §8 и решение
5.1 разбора аудита 14 августа 2026 требуют не пускать отчёт, посчитанный на
непригодном весе.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat

from .statistics import effective_sample_size

# Вес, отличающийся от среднего более чем в пять раз в любую сторону, считается
# экстремальным. Пять — обычная граница подрезки весов в практике опросов: она
# оставляет место естественному разбросу поправок и ловит хвосты, из-за которых
# несколько респондентов начинают определять результат.
EXTREME_WEIGHT_RATIO = 5.0

# Доля экстремальных значений, выше которой вес не принимается.
EXTREME_SHARE_LIMIT = 0.05

# Во сколько раз допустимо потерять эффективную базу. Design effect 3 означает,
# что 1000 интервью работают как 333: дальше начинать расчёт бессмысленно.
DESIGN_EFFECT_LIMIT = 3.0

WEIGHT_ROLE = "weight"


@dataclass(frozen=True, slots=True)
class WeightProblem:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class WeightDiagnostics:
    """Числа, которые обязательно показываются до применения веса."""

    count: int
    minimum: float
    maximum: float
    mean: float
    extreme_count: int
    extreme_share_percent: float
    effective_base: float
    design_effect: float
    efficiency_percent: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "mean": self.mean,
            "extreme_count": self.extreme_count,
            "extreme_share_percent": self.extreme_share_percent,
            "effective_base": self.effective_base,
            "design_effect": self.design_effect,
            "efficiency_percent": self.efficiency_percent,
        }


@dataclass(frozen=True, slots=True)
class WeightAssessment:
    variable: str
    role: str | None
    problems: list[WeightProblem]
    diagnostics: WeightDiagnostics | None = None
    # Замечания, которые не блокируют сборку: показываются рядом с числами,
    # чтобы аналитик увидел бесполезный вес до того, как удивится отчёту.
    notes: list[str] | None = None

    @property
    def usable(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable": self.variable,
            "role": self.role,
            "usable": self.usable,
            "problems": [problem.to_dict() for problem in self.problems],
            "notes": list(self.notes or []),
            "diagnostics": self.diagnostics.to_dict() if self.diagnostics else None,
        }


class WeightNotUsableError(ValueError):
    """Выбранная переменная не может быть весом отчёта."""

    def __init__(self, assessment: WeightAssessment) -> None:
        super().__init__(" ".join(problem.message for problem in assessment.problems))
        self.assessment = assessment
        # Первая проблема определяет код ответа: они упорядочены от причины к
        # следствию, и аналитику полезнее «не объявлена весом», чем «хвосты».
        self.code = assessment.problems[0].code if assessment.problems else "WEIGHT_NOT_USABLE"


def weight_role(variable: str, project: dict[str, Any]) -> str | None:
    """Роль, назначенную переменной аналитиком, а не выведенную из имени.

    Истина живёт в `configuration.questions`: именно её меняет действие
    «объявить весом». `inspection.variables` хранит первичное автоопределение и
    после ручной правки устаревает.
    """

    questions = project.get("configuration", {}).get("questions", [])
    for question in questions:
        if variable in (question.get("source_variables") or []):
            role = question.get("role")
            return str(role) if role is not None else None
    for item in project.get("inspection", {}).get("variables", []):
        if item.get("name") == variable:
            role = item.get("role")
            return str(role) if role is not None else None
    return None


def assess_ready_weight(
    values: pd.Series,
    *,
    variable: str,
    role: str | None,
) -> WeightAssessment:
    """Оценить пригодность колонки как готового веса. Ввода-вывода не делает."""

    problems: list[WeightProblem] = []
    notes: list[str] = []

    if role != WEIGHT_ROLE:
        problems.append(
            WeightProblem(
                "WEIGHT_ROLE_REQUIRED",
                f"Переменная {variable} не объявлена весом: у неё роль "
                f"«{_role_title(role)}». Весом может быть только переменная с ролью «Вес» — "
                "объявите её весом в структуре, если это действительно вес.",
            )
        )

    numeric = pd.to_numeric(values, errors="coerce")
    coerced = int((numeric.isna() & values.notna()).sum())
    missing = int(values.isna().sum())
    if coerced:
        problems.append(
            WeightProblem(
                "WEIGHT_NOT_NUMERIC",
                f"Весовая переменная {variable} должна быть числовой: "
                f"{coerced} значений не читаются как число.",
            )
        )
    if missing:
        problems.append(
            WeightProblem(
                "WEIGHT_HAS_MISSING",
                f"Весовая переменная {variable} заполнена не у всех респондентов: "
                f"пропусков {missing}.",
            )
        )
    if coerced or missing:
        return WeightAssessment(variable, role, problems, None, notes)

    if not len(numeric):
        problems.append(
            WeightProblem(
                "WEIGHT_HAS_MISSING",
                f"Весовая переменная {variable} не содержит ни одного значения.",
            )
        )
        return WeightAssessment(variable, role, problems, None, notes)

    weights = numeric.astype(float)
    non_positive = int((~weights.map(math.isfinite) | (weights <= 0)).sum())
    if non_positive:
        problems.append(
            WeightProblem(
                "WEIGHT_NOT_POSITIVE",
                f"Все веса должны быть конечными положительными числами: "
                f"у переменной {variable} таких значений {non_positive}.",
            )
        )
        return WeightAssessment(variable, role, problems, None, notes)

    diagnostics = weight_diagnostics(weights)
    if diagnostics.extreme_share_percent > EXTREME_SHARE_LIMIT * 100:
        problems.append(
            WeightProblem(
                "WEIGHT_EXTREME_VALUES",
                f"У {diagnostics.extreme_count} респондентов вес отличается от среднего "
                f"более чем в {_number(EXTREME_WEIGHT_RATIO)} раз — это "
                f"{_number(diagnostics.extreme_share_percent)}% при пороге "
                f"{_number(EXTREME_SHARE_LIMIT * 100)}%. "
                f"Разброс {_number(diagnostics.minimum)}…{_number(diagnostics.maximum)} "
                f"при среднем {_number(diagnostics.mean)} не похож на поправочный вес.",
            )
        )
    if diagnostics.design_effect > DESIGN_EFFECT_LIMIT:
        problems.append(
            WeightProblem(
                "WEIGHT_DESIGN_EFFECT",
                f"Design effect {_number(diagnostics.design_effect)} выше порога "
                f"{_number(DESIGN_EFFECT_LIMIT)}: {diagnostics.count} интервью работают как "
                f"{_number(diagnostics.effective_base)}.",
            )
        )

    if diagnostics.minimum == diagnostics.maximum:
        notes.append(
            "Вес постоянный: взвешенный отчёт совпадёт с невзвешенным, "
            "кроме строки «База, взвеш.»."
        )

    return WeightAssessment(variable, role, problems, diagnostics, notes)


def weight_diagnostics(weights: pd.Series) -> WeightDiagnostics:
    """Диагностика распределения весов. Веса уже проверены на положительность."""

    mean = float(weights.mean())
    extreme = (weights > mean * EXTREME_WEIGHT_RATIO) | (weights < mean / EXTREME_WEIGHT_RATIO)
    extreme_count = int(extreme.sum())
    count = int(len(weights))
    effective_base = effective_sample_size(weights)
    return WeightDiagnostics(
        count=count,
        minimum=float(weights.min()),
        maximum=float(weights.max()),
        mean=mean,
        extreme_count=extreme_count,
        extreme_share_percent=extreme_count / count * 100,
        effective_base=effective_base,
        design_effect=count / effective_base,
        efficiency_percent=effective_base / count * 100,
    )


def read_weight_column(path: str | Path, variable: str) -> pd.Series:
    """Прочитать одну колонку SAV.

    Проверка веса стоит перед сохранением настроек, то есть на пути обычного
    клика в интерфейсе. Читать ради неё весь массив не нужно.
    """

    frame, _ = pyreadstat.read_sav(
        path,
        usecols=[variable],
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    if variable not in frame.columns:
        raise KeyError(variable)
    return frame[variable]


def assess_project_weight(
    path: str | Path,
    variable: str,
    project: dict[str, Any],
) -> WeightAssessment:
    try:
        values = read_weight_column(path, variable)
    except KeyError:
        return WeightAssessment(
            variable,
            weight_role(variable, project),
            [
                WeightProblem(
                    "WEIGHT_VARIABLE_NOT_FOUND",
                    f"Весовая переменная {variable} не найдена в SAV.",
                )
            ],
        )
    return assess_ready_weight(values, variable=variable, role=weight_role(variable, project))


def ensure_project_weight_usable(
    path: str | Path,
    variable: str,
    project: dict[str, Any],
) -> WeightAssessment:
    assessment = assess_project_weight(path, variable, project)
    if not assessment.usable:
        raise WeightNotUsableError(assessment)
    return assessment


def _role_title(role: str | None) -> str:
    return {
        "question": "Вопрос",
        "id": "Идентификатор",
        "weight": "Вес",
        "wave": "Волна",
        "filter": "Фильтр",
        "technical": "Техническая",
    }.get(role or "", "не задана")


def _number(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if text else "0"


__all__ = [
    "DESIGN_EFFECT_LIMIT",
    "EXTREME_SHARE_LIMIT",
    "EXTREME_WEIGHT_RATIO",
    "WEIGHT_ROLE",
    "WeightAssessment",
    "WeightDiagnostics",
    "WeightNotUsableError",
    "WeightProblem",
    "assess_project_weight",
    "assess_ready_weight",
    "ensure_project_weight_usable",
    "read_weight_column",
    "weight_diagnostics",
    "weight_role",
]
