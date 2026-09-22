"""Модели раздела «Анализ»: линейная и логистическая регрессия (PQ.11).

Числа считает только этот модуль, экран лишь показывает результат.

- Линейная — взвешенный МНК, логистическая — IRLS. Категориальный предиктор
  кодируется фиктивными переменными; базовая категория — первая непустая, и
  результат её называет.
- Без веса ошибки модельные: `σ²(X'X)⁻¹` у линейной, обратная информация
  Фишера у логистической. На весе отчёта — сэндвич-оценка
  `(X'WX)⁻¹ [Σ wᵢ² sᵢ sᵢ'] (X'WX)⁻¹ · n/(n−1)`, где `sᵢ` — вклад наблюдения
  в оценочное уравнение: так считает `survey::svyglm` для выборки с весами
  без страт и кластеров.
- Пропуски — явный выбор, а не молчаливое заполнение, как у Qualtrics:
  `listwise` исключает неполные строки, `missing_category` делает пропуск
  категориального предиктора отдельной категорией. База до и после видна.
- Модель не строится, если на параметр приходится меньше
  `OBSERVATIONS_PER_PARAMETER` наблюдений: коэффициенты на такой базе
  выглядят точными и ничего не значат.
- У линейной модели считается относительная важность драйверов по Джонсону
  (2000): доли R², которые приходятся на каждый предиктор с учётом их
  взаимной корреляции.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from .association import AssociationError, Variable, load_analysis_data, resolve_variable
from .statistics import effective_sample_size

OBSERVATIONS_PER_PARAMETER = 10
MISSING_LABEL = "Нет ответа"
MAX_ITERATIONS = 50


class ModelError(ValueError):
    pass


@dataclass
class Design:
    matrix: np.ndarray
    names: list[str]
    groups: list[str]  # к какому предиктору относится столбец
    rows: pd.Series
    references: dict[str, str]


def fit_models(
    path: str | Path, project: dict[str, Any], models: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Все сохранённые модели проекта — на общем фильтре и весе отчёта."""
    if not models:
        return []
    data = load_analysis_data(
        path, project, [[model["dependent"], *model["predictors"]] for model in models]
    )
    results = []
    for position, model in enumerate(models):
        head = {"id": model.get("id"), "kind": model.get("kind", "linear")}
        problem = data.problems.get(position) or data.weight_problem
        if problem or data.frame is None:
            results.append({**head, "performed": False, "reason": problem or "Нет данных."})
            continue
        try:
            result = fit_model(model, project, data.frame, data.mask, data.weights)
        except ModelError as exc:
            results.append({**head, "performed": False, "reason": str(exc)})
            continue
        results.append({**result, **head, "filtered": data.mask is not None})
    return results


def fit_model(
    definition: dict[str, Any],
    project: dict[str, Any],
    frame: pd.DataFrame,
    mask: pd.Series | None = None,
    weights: pd.Series | None = None,
) -> dict[str, Any]:
    """Посчитать сохранённую модель: коэффициенты, качество, база и важность."""
    kind = definition.get("kind", "linear")
    missing = definition.get("missing", "listwise")
    try:
        dependent = resolve_variable(definition["dependent"], project, frame)
        predictors = [resolve_variable(item, project, frame) for item in definition["predictors"]]
    except AssociationError as exc:
        raise ModelError(str(exc)) from exc
    if not predictors:
        raise ModelError("Добавьте хотя бы один предиктор.")
    rows = pd.Series(True, index=frame.index) if mask is None else mask.fillna(False)
    base_before = int(rows.sum())

    if kind == "linear":
        if dependent.kind != "numeric":
            raise ModelError("Зависимая переменная линейной модели должна быть числовой.")
        outcome = dependent.series.astype(float)
        event = None
    elif kind == "logistic":
        outcome, event = _binary_outcome(dependent, definition.get("event"))
    else:
        raise ModelError("Неизвестный вид модели.")

    rows &= outcome.notna()
    design = _design(predictors, rows, missing)
    y = outcome[design.rows].to_numpy(dtype=float)
    x = design.matrix
    n = len(y)
    parameters = x.shape[1]
    result: dict[str, Any] = {
        "kind": kind,
        "dependent": dependent.label,
        "event": event,
        "missing": missing,
        "base_before": base_before,
        "base": n,
        "parameters": parameters,
        "references": design.references,
        "weighted": weights is not None,
    }
    needed = OBSERVATIONS_PER_PARAMETER * (parameters - 1)
    if n < max(needed, parameters + 1):
        return {
            **result,
            "performed": False,
            "reason": (
                f"На {parameters - 1} параметров модели нужно не меньше {needed} "
                f"наблюдений, а после отбора осталось {n}."
            ),
        }
    w = (
        np.ones(n)
        if weights is None
        else weights[design.rows].to_numpy(dtype=float) / float(weights[design.rows].mean())
    )
    if kind == "linear":
        fitted = _linear(x, y, w, weighted=weights is not None)
    else:
        if len(np.unique(y)) < 2:
            reason = "После отбора у зависимой одно значение."
            return {**result, "performed": False, "reason": reason}
        fitted = _logistic(x, y, w, weighted=weights is not None)
    if fitted.get("reason"):
        return {**result, "performed": False, "reason": fitted["reason"]}
    result.update(fitted)
    result["coefficients"] = [
        {**row, "name": name, "predictor": group}
        for row, name, group in zip(
            fitted["coefficients"], design.names, design.groups, strict=True
        )
    ]
    if weights is not None:
        result["effective_base"] = effective_sample_size(w)
    if kind == "linear" and parameters > 1:
        importance = relative_importance(x[:, 1:], y, w, design.groups[1:])
        # Карта драйверов: важность × оценка. Оценка — взвешенное среднее
        # числового предиктора на той же базе, что модель; у категориального
        # предиктора среднего нет, и на карту он не попадает.
        numeric = {variable.label for variable in predictors if variable.kind == "numeric"}
        for item in importance:
            if item["predictor"] in numeric:
                column = design.names.index(item["predictor"])
                item["mean"] = float(np.average(x[:, column], weights=w))
        result["importance"] = importance
    result["performed"] = True
    return result


def _binary_outcome(variable: Variable, event: str | None) -> tuple[pd.Series, str]:
    if variable.kind != "categorical":
        raise ModelError("Зависимая логистической модели — категориальная переменная.")
    filled = [(label, mask) for label, mask in variable.categories if mask.any()]
    if len(filled) != 2:
        raise ModelError(
            "У зависимой логистической модели должно быть ровно две заполненные "
            "категории — объедините остальные перекодировкой."
        )
    labels = [label for label, _ in filled]
    chosen = event if event in labels else labels[0]
    outcome = pd.Series(np.nan, index=variable.series.index)
    for label, mask in filled:
        outcome[mask] = 1.0 if label == chosen else 0.0
    return outcome, chosen


def _design(predictors: list[Variable], rows: pd.Series, missing: str) -> Design:
    """Матрица модели. Сначала общий отбор строк, потом столбцы.

    В два прохода, чтобы результат не зависел от порядка предикторов: иначе
    пропуски числового предиктора, идущего следом, могли бы опустошить уже
    построенный фиктивный столбец категориального.
    """
    rows = rows.copy()
    prepared: list[tuple[Variable, list[tuple[str, pd.Series]]]] = []
    for variable in predictors:
        if variable.kind == "numeric":
            rows &= variable.series.notna()
            prepared.append((variable, []))
            continue
        categories = list(variable.categories)
        answered = pd.Series(False, index=rows.index)
        for _, mask in categories:
            answered |= mask
        if missing == "missing_category":
            categories.append((MISSING_LABEL, ~answered))
        else:
            rows &= answered
        prepared.append((variable, categories))
    columns: list[pd.Series] = [pd.Series(1.0, index=rows.index)]
    names = ["Константа"]
    groups = ["Константа"]
    references: dict[str, str] = {}
    for variable, categories in prepared:
        if variable.kind == "numeric":
            columns.append(variable.series.astype(float))
            names.append(variable.label)
            groups.append(variable.label)
            continue
        present = [(label, mask) for label, mask in categories if (mask & rows).any()]
        if len(present) < 2:
            raise ModelError(f"У «{variable.label}» после отбора меньше двух категорий.")
        references[variable.label] = present[0][0]
        for label, mask in present[1:]:
            columns.append(mask.astype(float))
            names.append(f"{variable.label}: {label}")
            groups.append(variable.label)
    matrix = pd.concat(columns, axis=1)[rows].to_numpy(dtype=float)
    return Design(matrix=matrix, names=names, groups=groups, rows=rows, references=references)


def _linear(x: np.ndarray, y: np.ndarray, w: np.ndarray, *, weighted: bool) -> dict[str, Any]:
    n, p = x.shape
    xtwx = x.T @ (w[:, None] * x)
    if np.linalg.matrix_rank(xtwx) < p:
        return {"reason": "Предикторы линейно зависимы: уберите дублирующий предиктор."}
    bread = np.linalg.inv(xtwx)
    beta = bread @ (x.T @ (w * y))
    residuals = y - x @ beta
    mean = float(np.average(y, weights=w))
    total = float((w * (y - mean) ** 2).sum())
    explained = 1 - float((w * residuals**2).sum()) / total if total else 0.0
    degrees = n - p
    if weighted:
        covariance = _sandwich(bread, x, w * residuals, n)
        method = "Взвешенный МНК, ошибки — сэндвич-оценка по весу"
    else:
        sigma2 = float((residuals**2).sum()) / degrees
        covariance = sigma2 * bread
        method = "МНК"
    errors = np.sqrt(np.diag(covariance))
    statistics_ = beta / errors
    p_values = 2 * stats.t.sf(np.abs(statistics_), degrees)
    critical = stats.t.ppf(0.975, degrees)
    adjusted = 1 - (1 - explained) * (n - 1) / degrees if degrees > 0 else explained
    f_statistic, f_p = _overall_f(beta, covariance, p, degrees)
    return {
        "method": method,
        "r_squared": explained,
        "adjusted_r_squared": adjusted,
        "f_statistic": f_statistic,
        "f_p_value": f_p,
        "degrees_of_freedom": degrees,
        "coefficients": [
            {
                "estimate": float(beta[index]),
                "std_error": float(errors[index]),
                "statistic": float(statistics_[index]),
                "p_value": float(p_values[index]),
                "ci_low": float(beta[index] - critical * errors[index]),
                "ci_high": float(beta[index] + critical * errors[index]),
            }
            for index in range(p)
        ],
    }


def _overall_f(
    beta: np.ndarray, covariance: np.ndarray, p: int, degrees: int
) -> tuple[float | None, float | None]:
    """Совместный тест всех коэффициентов, кроме константы (тест Вальда)."""
    if p < 2:
        return None, None
    slopes = beta[1:]
    block = covariance[1:, 1:]
    try:
        wald = float(slopes @ np.linalg.solve(block, slopes))
    except np.linalg.LinAlgError:
        return None, None
    statistic = wald / (p - 1)
    return statistic, float(stats.f.sf(statistic, p - 1, degrees))


def _logistic(x: np.ndarray, y: np.ndarray, w: np.ndarray, *, weighted: bool) -> dict[str, Any]:
    n, p = x.shape
    beta = np.zeros(p)
    converged = False
    for _ in range(MAX_ITERATIONS):
        eta = np.clip(x @ beta, -30, 30)
        probability = 1 / (1 + np.exp(-eta))
        variance = probability * (1 - probability)
        information = x.T @ ((w * variance)[:, None] * x)
        if np.linalg.matrix_rank(information) < p:
            return {
                "reason": "Предикторы линейно зависимы или какая-то категория "
                "предсказывает исход без ошибок."
            }
        step = np.linalg.solve(information, x.T @ (w * (y - probability)))
        beta = beta + step
        if np.max(np.abs(step)) < 1e-10:
            converged = True
            break
    if not converged or np.max(np.abs(beta)) > 25:
        return {
            "reason": "Модель не сошлась: вероятно, полное разделение — какая-то "
            "категория предсказывает исход без ошибок. Объедините категории."
        }
    eta = x @ beta
    probability = 1 / (1 + np.exp(-eta))
    bread = np.linalg.inv(x.T @ ((w * probability * (1 - probability))[:, None] * x))
    if weighted:
        covariance = _sandwich(bread, x, w * (y - probability), n)
        method = "Логистическая регрессия на весе, ошибки — сэндвич-оценка"
    else:
        covariance = bread
        method = "Логистическая регрессия"
    errors = np.sqrt(np.diag(covariance))
    statistics_ = beta / errors
    p_values = 2 * stats.norm.sf(np.abs(statistics_))
    log_likelihood = float(
        (w * (y * np.log(probability) + (1 - y) * np.log(1 - probability))).sum()
    )
    share = float(np.average(y, weights=w))
    null = float((w * (y * math.log(share) + (1 - y) * math.log(1 - share))).sum())
    return {
        "method": method,
        "pseudo_r_squared": 1 - log_likelihood / null if null else 0.0,
        "log_likelihood": log_likelihood,
        "coefficients": [
            {
                "estimate": float(beta[index]),
                "std_error": float(errors[index]),
                "statistic": float(statistics_[index]),
                "p_value": float(p_values[index]),
                "odds_ratio": float(math.exp(beta[index])),
                "ci_low": float(math.exp(beta[index] - 1.959963984540054 * errors[index])),
                "ci_high": float(math.exp(beta[index] + 1.959963984540054 * errors[index])),
            }
            for index in range(p)
        ],
    }


def _sandwich(bread: np.ndarray, x: np.ndarray, scores: np.ndarray, n: int) -> np.ndarray:
    """Сэндвич: хлеб — обратная матрица оценочного уравнения, начинка — вклады."""
    meat = x.T @ ((scores**2)[:, None] * x)
    return bread @ meat @ bread * n / (n - 1)


def relative_importance(
    x: np.ndarray, y: np.ndarray, w: np.ndarray, groups: list[str]
) -> list[dict[str, Any]]:
    """Относительные веса Джонсона: доля R² каждого предиктора.

    Предикторы заменяются ближайшими к ним ортогональными `Z`; R² делится
    между `Z` без остатка и возвращается к исходным предикторам через их
    корреляции с `Z`. Сумма весов равна R² модели. Фиктивные столбцы одного
    категориального предиктора складываются.
    """
    correlation = _weighted_correlation_matrix(np.column_stack([x, y]), w)
    rxx = correlation[:-1, :-1]
    rxy = correlation[:-1, -1]
    eigenvalues, eigenvectors = np.linalg.eigh(rxx)
    eigenvalues = np.clip(eigenvalues, 0, None)
    loadings = eigenvectors @ np.diag(np.sqrt(eigenvalues)) @ eigenvectors.T
    beta = np.linalg.lstsq(loadings, rxy, rcond=None)[0]
    raw = (loadings**2) @ (beta**2)
    total = float(raw.sum())
    by_group: dict[str, float] = {}
    for group, value in zip(groups, raw, strict=True):
        by_group[group] = by_group.get(group, 0.0) + float(value)
    return [
        {
            "predictor": group,
            "weight": value,
            "share": value / total if total else 0.0,
        }
        for group, value in by_group.items()
    ]


def _weighted_correlation_matrix(data: np.ndarray, w: np.ndarray) -> np.ndarray:
    mean = (w[:, None] * data).sum(axis=0) / w.sum()
    centred = data - mean
    covariance = centred.T @ (w[:, None] * centred)
    scale = np.sqrt(np.diag(covariance))
    scale[scale == 0] = 1.0
    return covariance / np.outer(scale, scale)
