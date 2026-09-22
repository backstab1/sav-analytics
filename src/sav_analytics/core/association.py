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
оба. Если отчёт взвешен, карточка считается на том же весе: доли, средние и
корреляции взвешенные, а размер выборки в тестах — эффективная база Киша, как
у z- и t-тестов книги (инвариант 4). Такие p-value приближённые, и карточка
говорит об этом. Точный тест Фишера на весах не выполняется. После Welch
ANOVA пары групп разбирает Games–Howell. Формулировка вывода — шаблон по силе
эффекта, без языковой модели.
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
from .statistics import (
    chi_square_test,
    effective_sample_size,
    games_howell,
    weighted_correlation,
    weighted_welch_anova,
    weighted_welch_t_test,
    welch_anova,
    welch_t_test,
)


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
    settings = configuration.get("report_settings") or {}
    weight_problem = None
    try:
        for name in _weight_columns(settings, project):
            if name not in columns:
                columns.append(name)
    except AssociationError as exc:
        weight_problem = str(exc)
    frame = read_project_frame(path, project, columns) if columns else None
    mask = None
    if frame is not None and definition is not None:
        mask = evaluate_filter_frame(definition, project, frame)
    weights = None
    if frame is not None and weight_problem is None:
        try:
            weights = _report_weight_series(frame, settings, project)
        except AssociationError as exc:
            weight_problem = str(exc)
    results = []
    for position, card in enumerate(cards):
        if position in problems or weight_problem:
            results.append(_failed(card, problems.get(position) or str(weight_problem)))
            continue
        try:
            results.append(analyse_pair(card, project, frame, mask, weights=weights))
        except AssociationError as exc:
            results.append(_failed(card, str(exc)))
    adjust_benjamini_hochberg(results)
    for result in results:
        result["conclusion"] = _conclusion(result)
    return results


def _weight_columns(settings: dict[str, Any], project: dict[str, Any]) -> list[str]:
    """Столбцы SAV веса отчёта — готового или рассчитанного."""
    from .weighting import WeightingError, weight_columns

    if settings.get("weight_variable"):
        return [settings["weight_variable"]]
    if settings.get("calculated_weight_id"):
        definition = next(
            (
                item
                for item in project["configuration"].get("calculated_weights", [])
                if item["id"] == str(settings["calculated_weight_id"])
            ),
            None,
        )
        if definition is None:
            raise AssociationError("Рассчитанный вес отчёта не найден в проекте.")
        try:
            return weight_columns(definition, project)
        except WeightingError as exc:
            raise AssociationError(str(exc)) from exc
    return []


def _report_weight_series(
    frame: pd.DataFrame, settings: dict[str, Any], project: dict[str, Any]
) -> pd.Series | None:
    """Вес отчёта тем же кодом, что у книги, — или None без веса.

    Непригодный вес — отказ карточки с причиной, а не невзвешенный расчёт:
    иначе карточка и книга молча разошлись бы.
    """
    from .reporting.data import ReportError, _report_weights

    try:
        weights, _ = _report_weights(
            frame, settings.get("weight_variable"), settings.get("calculated_weight_id"), project
        )
    except ReportError as exc:
        raise AssociationError(f"Вес отчёта непригоден: {exc}") from exc
    return weights


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
    weights: pd.Series | None = None,
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
    if weights is not None:
        base["weighted"] = True
        base["note"] = "Взвешено; p-value приближённый — по эффективной базе Киша."
    rows = pd.Series(True, index=frame.index) if mask is None else mask.fillna(False)
    if first.kind == "categorical" and second.kind == "categorical":
        return {**base, **_categorical_pair(first, second, rows, weights)}
    if first.kind == "numeric" and second.kind == "numeric":
        return {**base, **numeric_correlation(first, second, rows, weights)}
    categorical, numeric = (first, second) if first.kind == "categorical" else (second, first)
    return {**base, **_means_pair(categorical, numeric, rows, weights)}


def _categorical_pair(
    first: Variable, second: Variable, rows: pd.Series, weights: pd.Series | None = None
) -> dict[str, Any]:
    counts = np.array(
        [
            [int((rows & row_mask & column_mask).sum()) for _, column_mask in second.categories]
            for _, row_mask in first.categories
        ],
        dtype=float,
    )
    keep_rows = counts.sum(axis=1) > 0
    keep_columns = counts.sum(axis=0) > 0
    counts = counts[keep_rows][:, keep_columns]
    n = int(counts.sum())
    if counts.shape[0] < 2 or counts.shape[1] < 2:
        return _skipped(
            "cramers_v", n, "Нужны хотя бы две заполненные категории у каждой переменной."
        )
    table = counts
    if weights is not None:
        # Взвешенный хи-квадрат выводится только с поправкой Rao–Scott, как
        # общий тест книги (PQ.6): приближение через n_eff для него не принято.
        # Сила связи по взвешенной таблице от этого не зависит и показывается.
        sums = np.array(
            [
                [
                    float(weights[rows & row_mask & column_mask].sum())
                    for _, column_mask in second.categories
                ]
                for _, row_mask in first.categories
            ]
        )[keep_rows][:, keep_columns]
        return {
            **_table_summary(first, second, counts, keep_rows, keep_columns, n),
            "effect": _cramers_v(sums),
            "performed": False,
            "method": "Хи-квадрат Пирсона",
            "reason": "Данные взвешены: хи-квадрату нужна поправка Rao–Scott, она ещё "
            "не реализована. V Крамера посчитан по взвешенной таблице.",
        }
    chi = chi_square_test(table, confidence_level=0.95, minimum_base=1)
    result: dict[str, Any] = {
        **_table_summary(first, second, counts, keep_rows, keep_columns, n),
        "effect": _cramers_v(table),
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


def _table_summary(
    first: Variable,
    second: Variable,
    counts: np.ndarray,
    keep_rows: np.ndarray,
    keep_columns: np.ndarray,
    n: int,
) -> dict[str, Any]:
    return {
        "effect_kind": "cramers_v",
        "n": n,
        "table": counts.astype(int).tolist(),
        "rows": [
            label for (label, _), keep in zip(first.categories, keep_rows, strict=True) if keep
        ],
        "columns": [
            label for (label, _), keep in zip(second.categories, keep_columns, strict=True) if keep
        ],
    }


def _cramers_v(table: np.ndarray) -> float:
    """V Крамера по таблице: зависит только от её долей, не от масштаба."""
    total = float(table.sum())
    expected = np.outer(table.sum(axis=1), table.sum(axis=0)) / total
    statistic = float(((table - expected) ** 2 / expected).sum())
    df_star = min(table.shape) - 1
    return math.sqrt(statistic / (total * df_star)) if total and df_star else 0.0


def _means_pair(
    categorical: Variable,
    numeric: Variable,
    rows: pd.Series,
    weights: pd.Series | None = None,
) -> dict[str, Any]:
    groups = []
    for label, mask in categorical.categories:
        selected = rows & mask & numeric.series.notna()
        values = numeric.series[selected].to_numpy(dtype=float)
        group_weights = (
            np.ones(len(values)) if weights is None else weights[selected].to_numpy(dtype=float)
        )
        if len(values) >= MINIMUM_GROUP:
            groups.append((label, values, group_weights))
    n = int(sum(len(values) for _, values, _ in groups))
    summary = [
        {
            "label": label,
            "n": int(len(values)),
            "mean": float(np.average(values, weights=group_weights)),
        }
        for label, values, group_weights in groups
    ]
    if len(groups) < 2:
        return {
            **_skipped("welch", n, "Нужны хотя бы две группы по два значения."),
            "groups": summary,
            "numeric_label": numeric.label,
        }
    base = {"n": n, "groups": summary, "numeric_label": numeric.label}
    stats_by_group = [_group_moments(values, group_weights) for _, values, group_weights in groups]
    if weights is not None:
        for item, moments in zip(summary, stats_by_group, strict=True):
            item["effective_base"] = moments[0]
    if len(groups) == 2:
        (_, a, weights_a), (_, b, weights_b) = groups
        if weights is None:
            result = welch_t_test(a, b, minimum_base=MINIMUM_GROUP)
        else:
            result = weighted_welch_t_test(
                a, weights_a, b, weights_b, minimum_base=MINIMUM_GROUP
            )
        (_, mean_a, var_a), (_, mean_b, var_b) = stats_by_group
        pooled = math.sqrt((var_a + var_b) / 2)
        cohens_d = float((mean_a - mean_b) / pooled) if pooled else 0.0
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
    if weights is None:
        result = welch_anova(
            [values for _, values, _ in groups], confidence_level=0.95, minimum_base=MINIMUM_GROUP
        )
    else:
        result = weighted_welch_anova(
            [values for _, values, _ in groups],
            [group_weights for _, _, group_weights in groups],
            confidence_level=0.95,
            minimum_base=MINIMUM_GROUP,
        )
    everything = np.concatenate([values for _, values, _ in groups])
    all_weights = np.concatenate([group_weights for _, _, group_weights in groups])
    grand = float(np.average(everything, weights=all_weights))
    between = sum(
        float(group_weights.sum()) * (moments[1] - grand) ** 2
        for (_, _, group_weights), moments in zip(groups, stats_by_group, strict=True)
    )
    total = float((all_weights * (everything - grand) ** 2).sum())
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
        "posthoc": _posthoc(groups, stats_by_group),
    }


def _group_moments(values: np.ndarray, weights: np.ndarray) -> tuple[float, float, float]:
    """Размер (n или n_eff), среднее и дисперсия группы — взвешенные, если есть вес."""
    mean = float(np.average(values, weights=weights))
    if np.all(weights == 1):
        return float(len(values)), mean, float(values.var(ddof=1)) if len(values) > 1 else 0.0
    denominator = weights.sum() - np.square(weights).sum() / weights.sum()
    variance = float((weights * (values - mean) ** 2).sum() / denominator) if denominator else 0.0
    return effective_sample_size(weights), mean, variance


def _posthoc(
    groups: list[tuple[str, np.ndarray, np.ndarray]],
    moments: list[tuple[float, float, float]],
) -> list[dict[str, Any]]:
    """Пары групп по Games–Howell — какие именно средние различаются."""
    pairs = games_howell(
        [item[0] for item in moments], [item[1] for item in moments], [item[2] for item in moments]
    )
    return [
        {
            "a": groups[pair.first][0],
            "b": groups[pair.second][0],
            "difference": pair.difference,
            "p_value": pair.p_value,
            "significant": pair.p_value < 0.05,
        }
        for pair in pairs
    ]


def numeric_correlation(
    first: Variable, second: Variable, rows: pd.Series, weights: pd.Series | None = None
) -> dict[str, Any]:
    """Связь двух числовых переменных: Пирсон, при выбросах — Спирмен.

    На весах — взвешенные коэффициенты и p-value по эффективной базе.
    """
    both = pd.concat([first.series[rows], second.series[rows]], axis=1).dropna()
    n = len(both)
    if n < 3 or both.iloc[:, 0].nunique() < 2 or both.iloc[:, 1].nunique() < 2:
        return _skipped("r", n, "Нужно хотя бы три пары значений с разбросом у обеих переменных.")
    x = both.iloc[:, 0].to_numpy(dtype=float)
    y = both.iloc[:, 1].to_numpy(dtype=float)
    outliers = max(_outlier_share(x), _outlier_share(y))
    spearman = outliers > OUTLIER_SHARE
    method = "Корреляция Спирмена" if spearman else "Корреляция Пирсона"
    note = (
        f"Выбросов больше {OUTLIER_SHARE:.0%} — выбрана ранговая корреляция."
        if spearman
        else None
    )
    effective = None
    if weights is None:
        statistic, p_value = (stats.spearmanr if spearman else stats.pearsonr)(x, y)
        degrees = n - 2
    else:
        statistic, p_value, effective = weighted_correlation(
            x, y, weights[both.index].to_numpy(dtype=float), ranks=spearman
        )
        degrees = effective - 2
    result = {
        "performed": True,
        "method": method,
        "n": n,
        "statistic": float(statistic),
        "degrees_of_freedom": [degrees],
        "p_value": float(p_value),
        "effect_kind": "r",
        "effect": float(statistic),
    }
    if effective is not None:
        result["effective_base"] = effective
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


def adjust_benjamini_hochberg(results: list[dict[str, Any]]) -> None:
    """Поправка на множественность по всем выполненным тестам набора."""
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


def _cards(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        word = "карточку"
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        word = "карточки"
    else:
        word = "карточек"
    return f"{count} {word}"


def _conclusion(result: dict[str, Any]) -> str:
    if not result.get("performed"):
        return f"Тест не выполнен: {result.get('reason', 'нет данных')}"
    effect_name = EFFECT_NAMES[result["effect_kind"]]
    p_text = (
        f"p {_p(result['p_value'])}, с поправкой на {_cards(result['comparisons'])} "
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


def variable_profile(
    path: str | Path, project: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any]:
    """Карточка переменной: распределение или среднее с разбросом и пропуски.

    Считается по той же подготовке, что карточки связи: учитывается общий
    фильтр отчёта и исключённые ответы шкалы.
    """
    columns = association_columns([source], project)
    configuration = project["configuration"]
    definition = None
    report_filter_id = configuration.get("report_filter_id")
    if report_filter_id:
        definition = next(
            (item for item in configuration.get("filters", []) if item["id"] == report_filter_id),
            None,
        )
        if definition is not None:
            from .filtering import filter_columns

            columns = list(dict.fromkeys([*columns, *filter_columns(definition, project)]))
    frame = read_project_frame(path, project, columns)
    rows = pd.Series(True, index=frame.index)
    if definition is not None:
        rows = evaluate_filter_frame(definition, project, frame).fillna(False)
    variable = resolve_variable(source, project, frame)
    total = int(rows.sum())
    profile = {
        "label": variable.label,
        "kind": variable.kind,
        "total": total,
        "filtered": definition is not None,
    }
    if variable.kind == "categorical":
        counts = [
            {
                "label": label,
                "count": int((rows & mask).sum()),
            }
            for label, mask in variable.categories
        ]
        answered = sum(item["count"] for item in counts)
        for item in counts:
            item["share"] = item["count"] / answered if answered else None
        return {
            **profile,
            "answered": answered,
            "missing": total - answered,
            "categories": sorted(counts, key=lambda item: -item["count"]),
        }
    values = variable.series[rows].dropna()
    if values.empty:
        return {**profile, "answered": 0, "missing": total, "statistics": None}
    return {
        **profile,
        "answered": int(values.size),
        "missing": total - int(values.size),
        "statistics": {
            "mean": float(values.mean()),
            "median": float(values.median()),
            "std": float(values.std(ddof=1)) if values.size > 1 else None,
            "min": float(values.min()),
            "max": float(values.max()),
            "q1": float(values.quantile(0.25)),
            "q3": float(values.quantile(0.75)),
        },
    }

