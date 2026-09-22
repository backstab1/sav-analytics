"""Методы исследования раздела «Анализ»: TURF, Van Westendorp, Gabor–Granger (PQ.15).

Каждый метод считается на общем фильтре и весе отчёта, как карточки и
модели, и возвращает вместе с результатом базу и то, что было исключено.

- TURF: какие k вариантов multiple-response вместе охватывают больше всего
  респондентов. Охват — доля тех, кто выбрал хотя бы один вариант портфеля,
  частота — среднее число выбранных из него. До `EXACT_LIMIT` сочетаний
  перебираются все, дальше — жадный отбор, и результат об этом говорит.
- Van Westendorp: четыре ценовых вопроса. Респонденты с нарушенным порядком
  «слишком дёшево ≤ дёшево ≤ дорого ≤ слишком дорого» исключаются и
  считаются. Кривые — накопленные доли, точки — пересечения кривых с
  линейной интерполяцией между соседними ценами.
- Gabor–Granger: вопросы «купите ли по цене P». Спрос — доля ответивших
  «куплю» на каждой цене, выручка — цена × спрос, оптимум — максимум выручки.
"""

from __future__ import annotations

from itertools import combinations
from math import comb
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .association import AssociationError, load_analysis_data
from .multiple_response import answered_mask, is_multiple, response_options, selected_mask
from .not_applicable import applicable_series

EXACT_LIMIT = 200_000
MAX_PORTFOLIO = 8
MAX_OPTIONS = 60


class MethodError(ValueError):
    pass


def _question(project: dict[str, Any], code: str) -> dict[str, Any]:
    question = next(
        (item for item in project["configuration"]["questions"] if item["code"] == code), None
    )
    if question is None:
        raise MethodError(f"Вопрос {code} не найден.")
    return question


def _context(
    path: str | Path, project: dict[str, Any], codes: list[str]
) -> tuple[pd.DataFrame, pd.Series, np.ndarray | None]:
    data = load_analysis_data(
        path, project, [[{"kind": "question", "ref": code} for code in codes]]
    )
    problem = data.problems.get(0) or data.weight_problem
    if problem or data.frame is None:
        raise MethodError(problem or "Нет данных.")
    rows = (
        pd.Series(True, index=data.frame.index) if data.mask is None else data.mask.fillna(False)
    )
    weights = None if data.weights is None else data.weights.to_numpy(dtype=float)
    return data.frame, rows, weights


def _weighted_share(mask: np.ndarray, weights: np.ndarray) -> float:
    total = float(weights.sum())
    return float(weights[mask].sum()) / total if total else 0.0


# --- TURF ---------------------------------------------------------------------


def turf(
    path: str | Path, project: dict[str, Any], code: str, max_size: int = 3
) -> dict[str, Any]:
    question = _question(project, code)
    if not is_multiple(question):
        raise MethodError("TURF строится по вопросу с несколькими ответами.")
    max_size = max(1, min(int(max_size), MAX_PORTFOLIO))
    try:
        frame, rows, weights = _context(path, project, [code])
    except AssociationError as exc:
        raise MethodError(str(exc)) from exc
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    options = response_options(question, variables, frame)
    if len(options) < 2:
        raise MethodError("Для TURF нужно хотя бы два варианта ответа.")
    # Набор выбранных вариантов кодируется битами 64-битного числа.
    if len(options) > MAX_OPTIONS:
        raise MethodError(f"Для TURF не больше {MAX_OPTIONS} вариантов ответа.")
    base = rows & answered_mask(frame, question)
    chosen = np.column_stack(
        [selected_mask(frame, question, option["key"])[base].to_numpy() for option in options]
    ).astype(bool)
    w = np.ones(len(chosen)) if weights is None else weights[base.to_numpy()]
    labels = [option["label"] for option in options]
    reach_single = [_weighted_share(chosen[:, index], w) for index in range(len(options))]
    patterns = _Patterns(chosen, w)
    portfolios = []
    for size in range(1, min(max_size, len(options)) + 1):
        exact = comb(len(options), size) <= EXACT_LIMIT
        best = (
            _best_exact(patterns, len(options), size)
            if exact
            else _best_greedy(patterns, len(options), size)
        )
        portfolios.append(
            {
                "size": size,
                "items": [labels[index] for index in best["items"]],
                "reach": best["reach"],
                "frequency": best["frequency"],
                "method": "полный перебор" if exact else "жадный отбор",
            }
        )
    return {
        "method": "TURF",
        "question": f"{question['code']} {question['label']}",
        "base": int(base.sum()),
        "weighted": weights is not None,
        "items": [
            {"label": label, "reach": reach}
            for label, reach in sorted(
                zip(labels, reach_single, strict=True), key=lambda item: -item[1]
            )
        ],
        "portfolios": portfolios,
    }


class _Patterns:
    """Ответы, сжатые в уникальные наборы выбранных вариантов с суммой весов.

    Охват и частота портфеля зависят только от набора выбранных вариантов,
    поэтому тысячи респондентов сводятся к сотням масок, и перебор сочетаний
    идёт по ним.
    """

    def __init__(self, chosen: np.ndarray, w: np.ndarray) -> None:
        bits = (chosen.astype(np.int64) << np.arange(chosen.shape[1], dtype=np.int64)).sum(axis=1)
        self.masks, inverse = np.unique(bits, return_inverse=True)
        self.weights = np.bincount(inverse, weights=w)
        self.total = float(w.sum())

    def score(self, items: tuple[int, ...] | list[int]) -> tuple[float, float]:
        portfolio = np.int64(sum(1 << index for index in items))
        hits = self.masks & portfolio
        if not self.total:
            return 0.0, 0.0
        reach = float(self.weights[hits != 0].sum()) / self.total
        frequency = float((self.weights * np.bitwise_count(hits)).sum()) / self.total
        return reach, frequency


def _better(candidate: tuple[float, float], best: tuple[float, float] | None) -> bool:
    """Больше охват, при равном — больше частота; иначе остаётся прежний."""
    if best is None:
        return True
    if candidate[0] > best[0] + 1e-12:
        return True
    return abs(candidate[0] - best[0]) <= 1e-12 and candidate[1] > best[1] + 1e-12


def _best_exact(patterns: _Patterns, count: int, size: int) -> dict[str, Any]:
    best: tuple[float, float] | None = None
    best_items: tuple[int, ...] = ()
    for items in combinations(range(count), size):
        scored = patterns.score(items)
        if _better(scored, best):
            best, best_items = scored, items
    assert best is not None
    return {"items": list(best_items), "reach": best[0], "frequency": best[1]}


def _best_greedy(patterns: _Patterns, count: int, size: int) -> dict[str, Any]:
    items: list[int] = []
    for _ in range(size):
        best: tuple[float, float] | None = None
        best_index = -1
        for index in range(count):
            if index in items:
                continue
            scored = patterns.score(items + [index])
            if _better(scored, best):
                best, best_index = scored, index
        items.append(best_index)
    reach, frequency = patterns.score(items)
    return {"items": items, "reach": reach, "frequency": frequency}


# --- Van Westendorp -------------------------------------------------------------


def _price_series(frame: pd.DataFrame, question: dict[str, Any]) -> pd.Series:
    sources = question.get("source_variables") or []
    if question["question_type"] not in {"numeric", "scale"} or len(sources) != 1:
        raise MethodError(f"{question['code']}: ценовой вопрос должен быть числовым.")
    return pd.to_numeric(applicable_series(frame[sources[0]], question), errors="coerce")


def van_westendorp(
    path: str | Path, project: dict[str, Any], codes: dict[str, str]
) -> dict[str, Any]:
    order = ("too_cheap", "cheap", "expensive", "too_expensive")
    missing = [key for key in order if not codes.get(key)]
    if missing:
        raise MethodError("Укажите все четыре ценовых вопроса.")
    questions = {key: _question(project, codes[key]) for key in order}
    try:
        frame, rows, weights = _context(path, project, [codes[key] for key in order])
    except AssociationError as exc:
        raise MethodError(str(exc)) from exc
    prices = pd.DataFrame({key: _price_series(frame, questions[key]) for key in order})
    complete = rows & prices.notna().all(axis=1)
    consistent = complete & (
        (prices["too_cheap"] <= prices["cheap"])
        & (prices["cheap"] <= prices["expensive"])
        & (prices["expensive"] <= prices["too_expensive"])
    )
    values = prices[consistent].to_numpy(dtype=float)
    w = np.ones(len(values)) if weights is None else weights[consistent.to_numpy()]
    if len(values) < 30:
        raise MethodError(
            f"Последовательно ответивших на все четыре вопроса {len(values)} — меньше 30."
        )
    grid = np.unique(values)
    total = float(w.sum())

    def share(column: int, below: bool) -> np.ndarray:
        data = values[:, column]
        if below:
            return np.array([float(w[data <= price].sum()) / total for price in grid])
        return np.array([float(w[data >= price].sum()) / total for price in grid])

    curves = {
        "too_cheap": share(0, below=False),
        "cheap": share(1, below=False),
        "expensive": share(2, below=True),
        "too_expensive": share(3, below=True),
    }
    points = {
        # Оптимальная цена: «слишком дёшево» и «слишком дорого» отвергают поровну.
        "optimal": _crossing(grid, curves["too_cheap"], curves["too_expensive"]),
        # Безразличная цена: «дёшево» и «дорого» называют одинаково часто.
        "indifference": _crossing(grid, curves["cheap"], curves["expensive"]),
        # Границы приемлемого диапазона: «слишком дёшево» против «не дёшево»
        # и «слишком дорого» против «не дорого».
        "marginal_cheapness": _crossing(grid, curves["too_cheap"], 1 - curves["cheap"]),
        # «Не дорого» убывает, «слишком дорого» растёт — порядок аргументов тот же.
        "marginal_expensiveness": _crossing(grid, 1 - curves["expensive"], curves["too_expensive"]),
    }
    return {
        "method": "Van Westendorp",
        "base": int(complete.sum()),
        "consistent_base": int(consistent.sum()),
        "excluded_inconsistent": int((complete & ~consistent).sum()),
        "weighted": weights is not None,
        "points": points,
        "curves": {
            "prices": grid.tolist(),
            **{key: value.tolist() for key, value in curves.items()},
        },
    }


def _crossing(grid: np.ndarray, falling: np.ndarray, rising: np.ndarray) -> float | None:
    """Цена, где убывающая кривая впервые опускается до растущей (линейно)."""
    difference = falling - rising
    for index in range(len(grid)):
        if difference[index] == 0:
            return float(grid[index])
        if index and difference[index - 1] > 0 > difference[index]:
            left, right = grid[index - 1], grid[index]
            share = difference[index - 1] / (difference[index - 1] - difference[index])
            return float(left + share * (right - left))
    return None


# --- Gabor–Granger --------------------------------------------------------------


def gabor_granger(
    path: str | Path, project: dict[str, Any], steps: list[dict[str, Any]]
) -> dict[str, Any]:
    if len(steps) < 2:
        raise MethodError("Нужно хотя бы две цены.")
    questions = [_question(project, step["code"]) for step in steps]
    try:
        frame, rows, weights = _context(path, project, [step["code"] for step in steps])
    except AssociationError as exc:
        raise MethodError(str(exc)) from exc
    points = []
    for step, question in zip(steps, questions, strict=True):
        sources = question.get("source_variables") or []
        if len(sources) != 1:
            raise MethodError(f"{question['code']}: нужен вопрос из одной переменной.")
        series = applicable_series(frame[sources[0]], question)
        answered = rows & series.notna()
        accepted = [str(value) for value in step["buy_values"]]
        buys = answered & series.map(lambda value, codes=accepted: _code(value) in codes)
        w = pd.Series(1.0 if weights is None else weights, index=frame.index)
        base = float(w[answered].sum())
        demand = float(w[buys].sum()) / base if base else 0.0
        points.append(
            {
                "code": question["code"],
                "price": float(step["price"]),
                "base": int(answered.sum()),
                "demand": demand,
                "revenue": float(step["price"]) * demand,
            }
        )
    points.sort(key=lambda item: item["price"])
    optimal = max(points, key=lambda item: item["revenue"])
    return {
        "method": "Gabor–Granger",
        "weighted": weights is not None,
        "points": points,
        "optimal_price": optimal["price"],
        "optimal_demand": optimal["demand"],
    }


def _code(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
