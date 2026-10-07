"""Стартовый вес в raking и ячейках.

Итог = стартовый вес × поправка. Эталоны ручные: в одномерном raking
поправка постоянна внутри категории, поэтому отношение итога к стартовому
весу внутри категории одно; в ячейках поправка ячейки — её цель, делённая
на её долю по стартовому весу.
"""

import pandas as pd
import pytest

from sav_analytics.core.weighting import (
    WeightingError,
    calculate_cell_weighting,
    calculate_raking,
    calculate_weight,
)

SEX = [1, 1, 1, 1, 1, 1, 2, 2, 2, 2]
START = [1.0, 1.0, 2.0, 2.0, 4.0, 4.0, 0.5, 0.5, 1.0, 1.0]


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"SEX": SEX, "W0": START})


def _sex(percent_men: float = 40.0) -> dict:
    return {
        "variable": "SEX",
        "label": "Пол",
        "targets": [
            {"label": "М", "values": [1], "percent": percent_men},
            {"label": "Ж", "values": [2], "percent": 100 - percent_men},
        ],
    }


def _shares(weights: pd.Series) -> float:
    men = pd.Series(SEX) == 1
    return float(weights[men].sum() / weights.sum())


def test_raking_keeps_start_weight_shape_inside_category() -> None:
    definition = {"dimensions": [_sex()], "base_weight": "W0",
                  "lower_bound": None, "upper_bound": None}
    result = calculate_raking(_frame(), definition)
    assert _shares(result.weights) == pytest.approx(0.4, abs=1e-6)
    ratio = result.weights / (pd.Series(START) / pd.Series(START).mean())
    men = pd.Series(SEX) == 1
    # Поправка одна внутри категории: форма веса отбора сохраняется.
    assert ratio[men].nunique() == 1 and ratio[~men].round(9).nunique() == 1
    assert result.weights.mean() == pytest.approx(1.0)
    # «До» — доля по стартовому весу: 14 из 17 у мужчин.
    before = result.diagnostics["distributions"][0]["categories"][0]["before_percent"]
    assert before == pytest.approx(14 / 17 * 100)
    start = result.diagnostics["start"]
    assert start["design_effect"] > 1


def test_without_start_weight_raking_is_unchanged() -> None:
    plain = calculate_raking(_frame(), {"dimensions": [_sex()]})
    ones = calculate_raking(_frame().assign(W0=1.0), {"dimensions": [_sex()], "base_weight": "W0"})
    assert plain.weights.tolist() == pytest.approx(ones.weights.tolist())
    assert "start" not in plain.diagnostics


def test_bounds_limit_the_adjustment_not_the_final_weight() -> None:
    definition = {"dimensions": [_sex(80.0)], "base_weight": "W0",
                  "lower_bound": 0.5, "upper_bound": 2.0}
    result = calculate_raking(_frame(), definition)
    start = pd.Series(START) / pd.Series(START).mean()
    factors = result.weights / start
    assert factors.max() <= 2.0 + 1e-9 and factors.min() >= 0.5 - 1e-9
    # Итоговый вес шире границ: его форму задаёт вес отбора.
    assert result.weights.max() > 2.0


def test_cells_adjust_start_weight_to_cell_targets() -> None:
    definition = {
        "dimensions": [{**_sex(), "targets": [
            {"label": "М", "values": [1]}, {"label": "Ж", "values": [2]},
        ]}],
        "cells": [{"categories": ["М"], "percent": 40}, {"categories": ["Ж"], "percent": 60}],
        "base_weight": "W0",
    }
    result = calculate_cell_weighting(_frame(), definition)
    assert _shares(result.weights) == pytest.approx(0.4)
    start = pd.Series(START) / pd.Series(START).mean()
    men = pd.Series(SEX) == 1
    # Поправка ячейки «М»: цель 40% к доле 14/17 по стартовому весу.
    assert (result.weights[men] / start[men]).tolist() == pytest.approx(
        [0.4 / (14 / 17)] * 6
    )
    cell = result.diagnostics["cells"][0]
    assert cell["before_percent"] == pytest.approx(14 / 17 * 100)


def test_start_weight_must_be_positive_everywhere() -> None:
    frame = _frame()
    frame.loc[3, "W0"] = 0
    frame.loc[4, "W0"] = float("nan")
    with pytest.raises(WeightingError, match="У 2 респондентов"):
        calculate_raking(frame, {"dimensions": [_sex()], "base_weight": "W0"})


def test_start_weight_needs_weight_role_in_project() -> None:
    project = {"configuration": {"questions": [
        {"code": "W0", "role": "question", "source_variables": ["W0"]},
    ]}}
    with pytest.raises(WeightingError, match="ролью «Вес»"):
        calculate_weight(_frame(), {"dimensions": [_sex()], "base_weight": "W0"}, project)
    project["configuration"]["questions"][0]["role"] = "weight"
    # Женщинам нужна поправка 3,4 — шире границ по умолчанию, поэтому без них.
    definition = {"dimensions": [_sex()], "base_weight": "W0",
                  "lower_bound": None, "upper_bound": None}
    result = calculate_weight(_frame(), definition, project)
    assert _shares(result.weights) == pytest.approx(0.4, abs=1e-6)
