"""Сверка с эталоном R (`survey`, `stats`): `tests/golden/survey_reference.json`.

Данные — `survey_reference_data.csv`, эталон пересчитывается
`Rscript tests/golden/survey_reference.R` (см. `tests/golden/README.md`).
Здесь R не запускается: тест сравнивает код приложения с числами, которые
R уже напечатал.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sav_analytics.core.association import Variable, _categorical_pair, numeric_correlation
from sav_analytics.core.regression import _linear, _logistic
from sav_analytics.core.statistics import (
    chi_square_test,
    rao_scott_chi_square,
    weighted_correlation,
    welch_anova,
)
from sav_analytics.core.weighting import (
    WeightingError,
    calculate_cell_weighting,
    calculate_greg,
    calculate_raking,
)

GOLDEN = Path(__file__).parent / "golden"
REFERENCE = json.loads((GOLDEN / "survey_reference.json").read_text(encoding="utf-8"))
DATA = pd.read_csv(GOLDEN / "survey_reference_data.csv")
TOLERANCE = 1e-9


def _close(actual, expected) -> None:
    assert np.asarray(actual, dtype=float) == pytest.approx(
        np.asarray(expected, dtype=float), rel=TOLERANCE, abs=1e-12
    )


def _codes(column: str) -> np.ndarray:
    return np.unique(DATA[column], return_inverse=True)[1]


@pytest.mark.parametrize(
    "case", REFERENCE["rao_scott"], ids=lambda case: case["rows"] + "×" + case["columns"]
)
def test_rao_scott_matches_svychisq(case: dict) -> None:
    result = rao_scott_chi_square(
        _codes(case["rows"]),
        _codes(case["columns"]),
        DATA["w"],
        confidence_level=0.95,
        minimum_base=1,
    )

    assert result.performed
    _close(result.statistic, case["statistic"])
    _close(result.degrees_of_freedom, case["df"])
    _close(result.p_value, case["p_value"])


def _categorical(column: str) -> Variable:
    series = DATA[column]
    return Variable(
        label=column,
        kind="categorical",
        series=series,
        categories=[(str(code), series == code) for code in sorted(series.unique())],
    )


def test_weighted_association_card_uses_rao_scott() -> None:
    case = REFERENCE["rao_scott"][0]
    rows = pd.Series(True, index=DATA.index)

    result = _categorical_pair(_categorical("region"), _categorical("grp"), rows, DATA["w"])

    assert result["performed"]
    assert "Rao–Scott" in result["method"]
    _close(result["p_value"], case["p_value"])


def test_pearson_chi_square_matches_chisq_test() -> None:
    table = pd.crosstab(DATA["region"], DATA["grp"]).to_numpy()
    result = chi_square_test(table, confidence_level=0.95, minimum_base=1)

    _close(result.statistic, REFERENCE["chi_square"]["statistic"])
    _close(result.p_value, REFERENCE["chi_square"]["p_value"])


def test_welch_anova_matches_oneway_test() -> None:
    groups = [DATA.loc[DATA["grp"] == code, "y"].to_numpy() for code in (1, 2, 3)]
    result = welch_anova(groups, confidence_level=0.95, minimum_base=2)

    _close(result.statistic, REFERENCE["welch_anova"]["statistic"])
    _close(result.degrees_of_freedom, REFERENCE["welch_anova"]["df"])
    _close(result.p_value, REFERENCE["welch_anova"]["p_value"])


def test_fisher_exact_matches_fisher_test() -> None:
    result = _categorical_pair(
        _categorical("sex"), _categorical("buy"), pd.Series(True, index=DATA.index)
    )
    # Ожидаемые частоты 2×2 здесь велики: карточка считает хи-квадрат, а
    # точный тест Фишера сверяется напрямую через SciPy на той же таблице.
    from scipy.stats import fisher_exact

    _, p_value = fisher_exact(pd.crosstab(DATA["sex"], DATA["buy"]).to_numpy())
    assert result["performed"]
    _close(p_value, REFERENCE["fisher"]["p_value"])


def test_pearson_correlation_matches_cor_test() -> None:
    rows = pd.Series(True, index=DATA.index)
    first = Variable(label="x1", kind="numeric", series=DATA["x1"])
    second = Variable(label="y", kind="numeric", series=DATA["y"])

    result = numeric_correlation(first, second, rows)

    assert result["method"] == "Корреляция Пирсона"
    _close(result["effect"], REFERENCE["pearson"]["r"])
    _close(result["p_value"], REFERENCE["pearson"]["p_value"])


@pytest.mark.parametrize("ranks", [False, True], ids=["pearson", "spearman"])
def test_weighted_correlation_with_unit_weights_matches_cor_test(ranks: bool) -> None:
    """Взвешенная корреляция на единичных весах — обычная, как `cor.test`."""
    r, p_value, _ = weighted_correlation(
        DATA["x1"].to_numpy(dtype=float),
        DATA["y"].to_numpy(dtype=float),
        np.ones(len(DATA)),
        ranks=ranks,
    )
    reference = REFERENCE["spearman" if ranks else "pearson"]

    _close(r, reference["r"])
    _close(p_value, reference["p_value"])


def _regression_matrix() -> np.ndarray:
    return np.column_stack(
        [
            np.ones(len(DATA)),
            DATA["x1"],
            DATA["x2"],
            (DATA["grp"] == 2).astype(float),
            (DATA["grp"] == 3).astype(float),
        ]
    )


def _check_coefficients(result: dict, reference: dict, *, p_values: bool = True) -> None:
    coefficients = result["coefficients"]
    _close([item["estimate"] for item in coefficients], reference["estimate"])
    _close([item["std_error"] for item in coefficients], reference["std_error"])
    _close([item["statistic"] for item in coefficients], reference["statistic"])
    if p_values:
        _close([item["p_value"] for item in coefficients], reference["p_value"])


def test_linear_regression_matches_lm() -> None:
    result = _linear(_regression_matrix(), DATA["y"].to_numpy(), np.ones(len(DATA)), weighted=False)
    reference = REFERENCE["linear"]

    _check_coefficients(result, reference)
    _close(result["r_squared"], reference["r_squared"])
    _close(result["adjusted_r_squared"], reference["adjusted_r_squared"])
    _close(result["f_statistic"], reference["f_statistic"])
    _close(result["f_p_value"], reference["f_p_value"])


def test_weighted_linear_regression_matches_svyglm() -> None:
    weights = (DATA["w"] / DATA["w"].mean()).to_numpy()
    result = _linear(_regression_matrix(), DATA["y"].to_numpy(), weights, weighted=True)

    _check_coefficients(result, REFERENCE["linear_weighted"])
    _close(
        [item["ci_low"] for item in result["coefficients"]], REFERENCE["linear_weighted"]["ci_low"]
    )
    _close(
        [item["ci_high"] for item in result["coefficients"]],
        REFERENCE["linear_weighted"]["ci_high"],
    )
    assert result["degrees_of_freedom"] == REFERENCE["linear_weighted"]["df_residual"]


def _logistic_matrix() -> np.ndarray:
    return np.column_stack([np.ones(len(DATA)), DATA["x1"], (DATA["sex"] == 2).astype(float)])


def test_logistic_regression_matches_glm() -> None:
    result = _logistic(
        _logistic_matrix(), DATA["buy"].to_numpy(dtype=float), np.ones(len(DATA)), weighted=False
    )

    _check_coefficients(result, REFERENCE["logistic"])


def test_weighted_logistic_regression_matches_svyglm() -> None:
    weights = (DATA["w"] / DATA["w"].mean()).to_numpy()
    result = _logistic(
        _logistic_matrix(), DATA["buy"].to_numpy(dtype=float), weights, weighted=True
    )

    reference = REFERENCE["logistic_weighted"]
    _check_coefficients(result, reference)
    # Карточка показывает интервал отношения шансов: exp от интервала R.
    _close([item["ci_low"] for item in result["coefficients"]], np.exp(reference["ci_low"]))
    _close([item["ci_high"] for item in result["coefficients"]], np.exp(reference["ci_high"]))


def _dimension(variable: str, shares: list[float]) -> dict:
    return {
        "variable": variable,
        "label": variable,
        "targets": [
            {"label": str(code), "values": [code], "percent": share * 100}
            for code, share in enumerate(shares, start=1)
        ],
    }


def _frame() -> pd.DataFrame:
    frame = DATA.copy()
    frame["w1"] = frame["w"] / frame["w"].mean()
    return frame


def _margins(reference: dict, **extra) -> dict:
    return {
        "dimensions": [_dimension("sex", reference["sex"]), _dimension("age", reference["age"])],
        "lower_bound": None,
        "upper_bound": None,
        **extra,
    }


@pytest.mark.parametrize("started", [False, True], ids=["plain", "started"])
def test_raking_matches_survey_rake(started: bool) -> None:
    reference = REFERENCE["rake"]
    definition = _margins(
        reference,
        tolerance=1e-12,
        maximum_iterations=5000,
        **({"base_weight": "w1"} if started else {}),
    )

    result = calculate_raking(_frame(), definition)

    _close(result.weights, reference["started" if started else "plain"])


@pytest.mark.parametrize("started", [False, True], ids=["plain", "started"])
def test_cell_weighting_matches_post_stratify(started: bool) -> None:
    reference = REFERENCE["cells"]
    targets = pd.DataFrame(reference["targets"])
    definition = {
        "method": "cells",
        "dimensions": [
            {
                **_dimension("sex", [0.5, 0.5]),
                "targets": [{"label": str(code), "values": [code]} for code in (1, 2)],
            },
            {
                **_dimension("age", [0.3, 0.4, 0.3]),
                "targets": [{"label": str(code), "values": [code]} for code in (1, 2, 3)],
            },
        ],
        "cells": [
            {"categories": [str(int(row.sex)), str(int(row.age))], "percent": row.share * 100}
            for row in targets.itertuples()
        ],
        **({"base_weight": "w1"} if started else {}),
    }

    result = calculate_cell_weighting(_frame(), definition)

    _close(result.weights, reference["started" if started else "plain"])


@pytest.mark.parametrize("case", ["plain", "started", "bounded"])
def test_greg_matches_survey_calibrate(case: str) -> None:
    reference = REFERENCE["greg"]
    expected = reference[case]
    bounds = expected.get("bounds") or [None, None]
    definition = _margins(
        reference,
        method="greg",
        lower_bound=bounds[0],
        upper_bound=bounds[1],
        tolerance=1e-9,
        **({"base_weight": "w1"} if case == "started" else {}),
    )

    result = calculate_greg(_frame(), definition)

    _close(result.weights, expected["weights"])


def test_greg_refuses_bounds_that_cannot_reach_the_targets() -> None:
    """При поправке 0,9–1,1 цели недостижимы: отказ, а не молчаливая обрезка."""
    definition = _margins(
        REFERENCE["greg"],
        method="greg",
        lower_bound=0.9,
        upper_bound=1.1,
        maximum_iterations=200,
    )

    with pytest.raises(WeightingError, match="GREG"):
        calculate_greg(_frame(), definition)
