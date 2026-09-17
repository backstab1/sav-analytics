"""Связь двух переменных: тест по типам, размер эффекта и вывод шаблоном (PQ.10).

Тест выбирается по типам источников, а не аналитиком:

- две категориальные — хи-квадрат Пирсона; при малых ожидаемых частотах в
  таблице 2×2 — точный тест Фишера, в большей таблице тест не выполняется с
  советом объединить категории. Размер эффекта — V Крамера;
- категориальная и числовая — Welch t-test для двух групп (d Коэна), Welch
  ANOVA для трёх и более (f Коэна);
- две числовые — корреляция Пирсона, при выбросах — Спирмена (r).

По всем карточкам рабочей области p-value корректируется Benjamini–Hochberg:
вывод «связаны» делается по скорректированному значению, а в карточке видны
оба. Взвешенные данные пока не поддерживаются — карточка говорит об этом, а
не считает невзвешенно молча. Формулировка вывода — шаблон по силе эффекта,
без языковой модели.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from .banner import BannerError, _source_categories
from .filtering import evaluate_filter_frame
from .formulas import read_project_frame
from .not_applicable import applicable_series
from .statistics import chi_square_test, welch_anova, welch_t_test


class AssociationError(ValueError):
    pass


CATEGORICAL_TYPES = {"single_choice"}
NUMERIC_TYPES = {"numeric", "scale"}
OUTLIER_Z = 3.0
OUTLIER_SHARE = 0.01
MINIMUM_GROUP = 2

# Пороги силы эффекта по Коэну: слабая, умеренная, сильная.
THRESHOLDS = {
    "cramers_v": (0.1, 0.3, 0.5),
    "cohens_d": (0.2, 0.5, 0.8),
    "cohens_f": (0.1, 0.25, 0.4),
    "r": (0.1, 0.3, 0.5),
}
EFFECT_NAMES = {
    "cramers_v": "V Крамера",
    "cohens_d": "d Коэна",
    "cohens_f": "f Коэна",
    "r": "r",
}
MAGNITUDES = ("пренебрежимо малая", "слабая", "умеренная", "сильная")


@dataclass
class Variable:
    label: str
    kind: str  # "categorical" | "numeric"
    series: pd.Series
    categories: list[tuple[str, pd.Series]] = field(default_factory=list)


def resolve_variable(
    source: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> Variable:
    configuration = project["configuration"]
    if source["kind"] == "recoding":
        try:
            resolved = _source_categories(source, project, frame)
        except BannerError as exc:
            raise AssociationError(str(exc)) from exc
        return _categorical(resolved["label"], resolved["categories"], frame.index)
    question = next(
        (item for item in configuration["questions"] if item["code"] == source["ref"]), None
    )
    if question is None:
        raise AssociationError(f"Вопрос {source['ref']} не найден.")
    sources = question.get("source_variables") or []
    if len(sources) != 1:
        raise AssociationError(
            f"{question['code']}: для связи нужен вопрос из одной переменной."
        )
    series = applicable_series(frame[sources[0]], question)
    label = f"{question['code']} {question['label']}"
    if question["question_type"] in CATEGORICAL_TYPES:
        try:
            resolved = _source_categories(source, project, frame)
        except BannerError as exc:
            raise AssociationError(str(exc)) from exc
        return _categorical(label, resolved["categories"], frame.index, series)
    if question["question_type"] in NUMERIC_TYPES:
        numeric = pd.to_numeric(series, errors="coerce")
        # Исключённые ответы шкалы («затрудняюсь») в средние не идут, как в отчёте.
        special = pd.to_numeric(pd.Series(question.get("special_values") or []), errors="coerce")
        numeric = numeric.mask(numeric.isin(special.dropna()))
        return Variable(label=label, kind="numeric", series=numeric.astype(float))
    raise AssociationError(
        f"{question['code']}: связь считается для одиночного выбора, шкалы и числового вопроса."
    )


def _categorical(
    label: str,
    categories: list[dict[str, Any]],
    index: pd.Index,
    series: pd.Series | None = None,
) -> Variable:
    pairs = [
        (category["label"], category["mask"].fillna(False).astype(bool))
        for category in categories
    ]
    assigned = pd.Series(False, index=index)
    for _, mask in pairs:
        assigned |= mask
    return Variable(
        label=label,
        kind="categorical",
        series=assigned if series is None else series,
        categories=pairs,
    )


def association_columns(sources: list[dict[str, Any]], project: dict[str, Any]) -> list[str]:
    from .banner import _source_columns

    columns: list[str] = []
    for source in sources:
        if source["kind"] == "question":
            question = next(
                (
                    item
                    for item in project["configuration"]["questions"]
                    if item["code"] == source["ref"]
                ),
                None,
            )
            if question is None:
                raise AssociationError(f"Вопрос {source['ref']} не найден.")
            names = list(question.get("source_variables") or [])
        else:
            try:
                names = _source_columns(source, project)
            except BannerError as exc:
                raise AssociationError(str(exc)) from exc
        for name in names:
            if name not in columns:
                columns.append(name)
    return columns


def analyse_cards(
    path: str | Path, project: dict[str, Any], cards: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Посчитать все карточки и скорректировать p-value по Benjamini–Hochberg."""
    if not cards:
        return []
    configuration = project["configuration"]
    columns: list[str] = []
    problems: dict[int, str] = {}
    for position, card in enumerate(cards):
        try:
            for name in association_columns([card["a"], card["b"]], project):
                if name not in columns:
                    columns.append(name)
        except AssociationError as exc:
            problems[position] = str(exc)
    report_filter_id = configuration.get("report_filter_id")
    definition = None
    if report_filter_id:
        definition = next(
            (item for item in configuration.get("filters", []) if item["id"] == report_filter_id),
            None,
        )
        if definition is not None:
            from .filtering import filter_columns

            for name in filter_columns(definition, project):
                if name not in columns:
                    columns.append(name)
    frame = read_project_frame(path, project, columns) if columns else None
    mask = None
    if frame is not None and definition is not None:
        mask = evaluate_filter_frame(definition, project, frame)
    weighted = bool(
        (configuration.get("report_settings") or {}).get("weight_variable")
        or (configuration.get("report_settings") or {}).get("calculated_weight_id")
    )
    results = []
    for position, card in enumerate(cards):
        if position in problems:
            results.append(_failed(card, problems[position]))
            continue
        try:
            results.append(analyse_pair(card, project, frame, mask, weighted=weighted))
        except AssociationError as exc:
            results.append(_failed(card, str(exc)))
    _benjamini_hochberg(results)
    for result in results:
        result["conclusion"] = _conclusion(result)
    return results


def _failed(card: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "a": card["a"],
        "b": card["b"],
        "performed": False,
        "reason": reason,
    }


def analyse_pair(
    card: dict[str, Any],
    project: dict[str, Any],
    frame: pd.DataFrame,
    mask: pd.Series | None = None,
    *,
    weighted: bool = False,
) -> dict[str, Any]:
    first = resolve_variable(card["a"], project, frame)
    second = resolve_variable(card["b"], project, frame)
    base = {
        "id": card.get("id"),
        "a": card["a"],
        "b": card["b"],
        "a_label": first.label,
        "b_label": second.label,
        "filtered": mask is not None,
    }
    if weighted:
        base["note"] = "Отчёт взвешен, а карточка связи пока считается без веса."
    rows = pd.Series(True, index=frame.index) if mask is None else mask.fillna(False)
    if first.kind == "categorical" and second.kind == "categorical":
        return {**base, **_categorical_pair(first, second, rows)}
    if first.kind == "numeric" and second.kind == "numeric":
        return {**base, **_numeric_pair(first, second, rows)}
    categorical, numeric = (first, second) if first.kind == "categorical" else (second, first)
    return {**base, **_means_pair(categorical, numeric, rows)}


def _categorical_pair(first: Variable, second: Variable, rows: pd.Series) -> dict[str, Any]:
    table = np.array(
        [
            [int((rows & row_mask & column_mask).sum()) for _, column_mask in second.categories]
            for _, row_mask in first.categories
        ],
        dtype=float,
    )
    keep_rows = table.sum(axis=1) > 0
    keep_columns = table.sum(axis=0) > 0
    table = table[keep_rows][:, keep_columns]
    n = int(table.sum())
    if table.shape[0] < 2 or table.shape[1] < 2:
        return _skipped(
            "cramers_v", n, "Нужны хотя бы две заполненные категории у каждой переменной."
        )
    chi = chi_square_test(table, confidence_level=0.95, minimum_base=1)
    expected = np.outer(table.sum(axis=1), table.sum(axis=0)) / n
    statistic = float(((table - expected) ** 2 / expected).sum())
    df_star = min(table.shape) - 1
    cramers_v = math.sqrt(statistic / (n * df_star)) if n and df_star else 0.0
    result: dict[str, Any] = {
        "effect_kind": "cramers_v",
        "effect": cramers_v,
        "n": n,
        "table": table.astype(int).tolist(),
        "rows": [
            label for (label, _), keep in zip(first.categories, keep_rows, strict=True) if keep
        ],
        "columns": [
            label for (label, _), keep in zip(second.categories, keep_columns, strict=True) if keep
        ],
    }
    if chi.performed:
        return {
            **result,
            "performed": True,
            "method": "Хи-квадрат Пирсона",
            "statistic": chi.statistic,
            "degrees_of_freedom": list(chi.degrees_of_freedom or ()),
            "p_value": chi.p_value,
        }
    if table.shape == (2, 2):
        _, p_value = stats.fisher_exact(table.astype(int))
        return {
            **result,
            "performed": True,
            "method": "Точный тест Фишера",
            "statistic": None,
            "degrees_of_freedom": [],
            "p_value": float(p_value),
            "note_method": "Ожидаемые частоты малы для хи-квадрата — выполнен точный тест.",
        }
    return {
        **result,
        "performed": False,
        "method": "Хи-квадрат Пирсона",
        "reason": chi.reason + " Объедините редкие категории перекодировкой.",
    }


def _means_pair(categorical: Variable, numeric: Variable, rows: pd.Series) -> dict[str, Any]:
    groups = []
    for label, mask in categorical.categories:
        values = numeric.series[rows & mask].dropna().to_numpy()
        if len(values) >= MINIMUM_GROUP:
            groups.append((label, values))
    n = int(sum(len(values) for _, values in groups))
    summary = [
        {"label": label, "n": int(len(values)), "mean": float(values.mean())}
        for label, values in groups
    ]
    if len(groups) < 2:
        return {
            **_skipped("welch", n, "Нужны хотя бы две группы по два значения."),
            "groups": summary,
            "numeric_label": numeric.label,
        }
    base = {"n": n, "groups": summary, "numeric_label": numeric.label}
    if len(groups) == 2:
        result = welch_t_test(groups[0][1], groups[1][1], minimum_base=MINIMUM_GROUP)
        (_, a), (_, b) = groups
        pooled = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
        cohens_d = float((a.mean() - b.mean()) / pooled) if pooled else 0.0
        if not result.performed:
            return {**_skipped("cohens_d", n, result.reason or "Тест не выполнен."), **base}
        return {
            **base,
            "performed": True,
            "method": "Welch t-test",
            "statistic": result.statistic,
            "degrees_of_freedom": [result.degrees_of_freedom]
            if result.degrees_of_freedom is not None
            else [],
            "p_value": result.p_value,
            "effect_kind": "cohens_d",
            "effect": cohens_d,
        }
    result = welch_anova(
        [values for _, values in groups], confidence_level=0.95, minimum_base=MINIMUM_GROUP
    )
    everything = np.concatenate([values for _, values in groups])
    grand = everything.mean()
    between = sum(len(values) * (values.mean() - grand) ** 2 for _, values in groups)
    total = float(((everything - grand) ** 2).sum())
    eta_squared = between / total if total else 0.0
    cohens_f = math.sqrt(eta_squared / (1 - eta_squared)) if eta_squared < 1 else float("inf")
    if not result.performed:
        return {**_skipped("cohens_f", n, result.reason or "Тест не выполнен."), **base}
    return {
        **base,
        "performed": True,
        "method": "Welch ANOVA",
        "statistic": result.statistic,
        "degrees_of_freedom": list(result.degrees_of_freedom or ()),
        "p_value": result.p_value,
        "effect_kind": "cohens_f",
        "effect": cohens_f,
    }


def _numeric_pair(first: Variable, second: Variable, rows: pd.Series) -> dict[str, Any]:
    both = pd.concat([first.series[rows], second.series[rows]], axis=1).dropna()
    n = len(both)
    if n < 3 or both.iloc[:, 0].nunique() < 2 or both.iloc[:, 1].nunique() < 2:
        return _skipped("r", n, "Нужно хотя бы три пары значений с разбросом у обеих переменных.")
    x = both.iloc[:, 0].to_numpy()
    y = both.iloc[:, 1].to_numpy()
    outliers = max(_outlier_share(x), _outlier_share(y))
    if outliers > OUTLIER_SHARE:
        statistic, p_value = stats.spearmanr(x, y)
        method = "Корреляция Спирмена"
        note = f"Выбросов больше {OUTLIER_SHARE:.0%} — выбрана ранговая корреляция."
    else:
        statistic, p_value = stats.pearsonr(x, y)
        method = "Корреляция Пирсона"
        note = None
    result = {
        "performed": True,
        "method": method,
        "n": n,
        "statistic": float(statistic),
        "degrees_of_freedom": [n - 2],
        "p_value": float(p_value),
        "effect_kind": "r",
        "effect": float(statistic),
    }
    if note:
        result["note_method"] = note
    return result


def _outlier_share(values: np.ndarray) -> float:
    spread = values.std(ddof=1)
    if not spread:
        return 0.0
    return float((np.abs((values - values.mean()) / spread) > OUTLIER_Z).mean())


def _skipped(effect_kind: str, n: int, reason: str) -> dict[str, Any]:
    return {"performed": False, "effect_kind": effect_kind, "n": n, "reason": reason}


def _benjamini_hochberg(results: list[dict[str, Any]]) -> None:
    performed = [result for result in results if result.get("performed")]
    count = len(performed)
    ordered = sorted(performed, key=lambda result: result["p_value"])
    adjusted = [0.0] * count
    running = 1.0
    for rank in range(count, 0, -1):
        value = ordered[rank - 1]["p_value"] * count / rank
        running = min(running, value)
        adjusted[rank - 1] = min(running, 1.0)
    for result, value in zip(ordered, adjusted, strict=True):
        result["p_adjusted"] = value
        result["significant"] = value < 0.05
        result["magnitude"] = magnitude(result["effect_kind"], result["effect"], result)
        result["comparisons"] = count


def magnitude(kind: str, effect: float, result: dict[str, Any] | None = None) -> str:
    small, medium, large = THRESHOLDS[kind]
    value = abs(effect)
    if kind == "cramers_v" and result and result.get("table"):
        # Пороги Коэна заданы для w; V = w / sqrt(min(r, c) − 1).
        df_star = min(len(result["table"]), len(result["table"][0])) - 1
        scale = math.sqrt(df_star) if df_star > 0 else 1.0
        small, medium, large = small / scale, medium / scale, large / scale
    if value >= large:
        return MAGNITUDES[3]
    if value >= medium:
        return MAGNITUDES[2]
    if value >= small:
        return MAGNITUDES[1]
    return MAGNITUDES[0]


def _number(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _p(value: float) -> str:
    return "< 0,001" if value < 0.001 else f"= {_number(value, 3)}"


def _conclusion(result: dict[str, Any]) -> str:
    if not result.get("performed"):
        return f"Тест не выполнен: {result.get('reason', 'нет данных')}"
    effect_name = EFFECT_NAMES[result["effect_kind"]]
    p_text = (
        f"p {_p(result['p_value'])}, с поправкой на {result['comparisons']} карт. "
        f"p {_p(result['p_adjusted'])}"
    )
    if not result["significant"]:
        return f"Связь не подтверждена ({p_text})."
    text = (
        f"Связь есть, сила — {result['magnitude']} "
        f"({effect_name} = {_number(result['effect'])}; {p_text})."
    )
    if result["effect_kind"] == "r":
        direction = "больше" if result["effect"] > 0 else "меньше"
        text += f" Чем выше первая переменная, тем {direction} вторая."
    if result.get("groups"):
        top = max(result["groups"], key=lambda group: group["mean"])
        low = min(result["groups"], key=lambda group: group["mean"])
        text += (
            f" Среднее выше всего у «{top['label']}» ({_number(top['mean'])}), "
            f"ниже всего у «{low['label']}» ({_number(low['mean'])})."
        )
    return text
