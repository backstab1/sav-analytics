"""Вес в «Анализе» и на листе Correlations, Games–Howell (PQ.10, §11).

Эталоны независимые: SciPy на невзвешенных или размноженных данных. Целые
частотные веса дают те же коэффициенты, что данные, размноженные этими
весами, — так проверяется взвешенная арифметика без собственного расчёта.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from sav_analytics.core.association import (
    Variable,
    _categorical_pair,
    _means_pair,
    analyse_cards,
    numeric_correlation,
)
from sav_analytics.core.statistics import (
    effective_sample_size,
    games_howell,
    weighted_correlation,
    weighted_welch_anova,
    weighted_welch_t_test,
    welch_anova,
    welch_t_test,
)
from tests.test_association import _project

GENERATOR = np.random.default_rng(7)
X = np.round(GENERATOR.normal(10, 3, 60), 1)
Y = np.round(X * 0.5 + GENERATOR.normal(0, 2, 60), 1)
FREQUENCY = GENERATOR.integers(1, 4, 60).astype(float)


def test_games_howell_of_two_groups_is_the_welch_t_test() -> None:
    """При k = 2 стьюдентизированный размах сводится к t: p-value совпадают."""
    a, b = X[:25], X[25:] + 1.5
    welch = welch_t_test(a, b, minimum_base=2)

    (pair,) = games_howell([len(a), len(b)], [a.mean(), b.mean()], [a.var(ddof=1), b.var(ddof=1)])

    assert pair.p_value == pytest.approx(welch.p_value, rel=1e-6)
    assert pair.degrees_of_freedom == pytest.approx(welch.degrees_of_freedom)


def test_weighted_games_howell_of_two_groups_is_the_weighted_welch() -> None:
    a, b = X[:25], X[25:] + 1.5
    weights_a, weights_b = FREQUENCY[:25], FREQUENCY[25:]
    welch = weighted_welch_t_test(a, weights_a, b, weights_b, minimum_base=2)
    means = [np.average(a, weights=weights_a), np.average(b, weights=weights_b)]

    (pair,) = games_howell(welch.effective_bases, means, welch.group_variances)

    assert pair.p_value == pytest.approx(welch.p_value, rel=1e-6)


def test_games_howell_finds_the_group_that_differs() -> None:
    groups = [X[:20], X[20:40], X[40:] + 4]

    pairs = games_howell(
        [len(group) for group in groups],
        [group.mean() for group in groups],
        [group.var(ddof=1) for group in groups],
    )

    by_pair = {(pair.first, pair.second): pair.p_value for pair in pairs}
    assert by_pair[(0, 2)] < 0.05 and by_pair[(1, 2)] < 0.05
    assert by_pair[(0, 1)] > 0.05


def test_weighted_correlation_with_frequency_weights_matches_replicated_data() -> None:
    replicated_x = np.repeat(X, FREQUENCY.astype(int))
    replicated_y = np.repeat(Y, FREQUENCY.astype(int))

    pearson, _, _ = weighted_correlation(X, Y, FREQUENCY)
    spearman, _, _ = weighted_correlation(X, Y, FREQUENCY, ranks=True)

    assert pearson == pytest.approx(stats.pearsonr(replicated_x, replicated_y)[0])
    assert spearman == pytest.approx(stats.spearmanr(replicated_x, replicated_y)[0])


def test_unit_weights_reproduce_the_unweighted_correlation_and_p_value() -> None:
    ones = np.ones(len(X))

    pearson, p_pearson, effective = weighted_correlation(X, Y, ones)
    spearman, p_spearman, _ = weighted_correlation(X, Y, ones, ranks=True)

    assert effective == pytest.approx(len(X))
    assert (pearson, p_pearson) == pytest.approx(tuple(stats.pearsonr(X, Y)))
    assert (spearman, p_spearman) == pytest.approx(tuple(stats.spearmanr(X, Y)))


def test_weighted_p_value_uses_the_effective_base() -> None:
    r, p_value, effective = weighted_correlation(X, Y, FREQUENCY)

    statistic = r * np.sqrt((effective - 2) / (1 - r**2))
    assert effective == pytest.approx(effective_sample_size(FREQUENCY))
    assert p_value == pytest.approx(2 * stats.t.sf(abs(statistic), effective - 2))


def test_weighted_welch_anova_with_unit_weights_is_welch_anova() -> None:
    groups = [X[:20], X[20:40], X[40:] + 2]

    plain = welch_anova(groups, confidence_level=0.95, minimum_base=2)
    weighted = weighted_welch_anova(
        groups, [np.ones(len(group)) for group in groups], confidence_level=0.95, minimum_base=2
    )

    assert weighted.statistic == pytest.approx(plain.statistic)
    assert weighted.p_value == pytest.approx(plain.p_value)


def _categorical(values: np.ndarray, label: str) -> Variable:
    series = pd.Series(values)
    return Variable(
        label=label,
        kind="categorical",
        series=series,
        categories=[(str(code), series == code) for code in sorted(set(values))],
    )


def test_weighted_chi_square_waits_for_rao_scott_but_shows_the_weighted_v() -> None:
    """Взвешенный хи-квадрат без Rao–Scott не выводится — как общий тест книги.

    V Крамера при этом считается по взвешенной таблице. Эталон: SciPy на
    таблице, размноженной частотными весами.
    """
    first = _categorical((X > 10).astype(int), "Выше 10")
    second = _categorical((Y > 5).astype(int), "Выше 5")
    rows = pd.Series(True, index=first.series.index)

    result = _categorical_pair(first, second, rows, pd.Series(FREQUENCY))

    replicated = pd.crosstab(
        np.repeat(first.series.to_numpy(), FREQUENCY.astype(int)),
        np.repeat(second.series.to_numpy(), FREQUENCY.astype(int)),
    ).to_numpy()
    assert not result["performed"]
    assert "Rao–Scott" in result["reason"]
    assert result["effect"] == pytest.approx(
        stats.contingency.association(replicated, method="cramer", correction=False)
    )
    # Таблица в карточке — невзвешенные числа респондентов.
    assert np.asarray(result["table"]).sum() == len(X)


def test_weighted_means_pair_reports_effective_bases_and_posthoc() -> None:
    region = _categorical(np.tile([1, 2, 3], 20), "Регион")
    score = Variable(label="Оценка", kind="numeric", series=pd.Series(X + (region.series == 3) * 4))
    rows = pd.Series(True, index=score.series.index)

    result = _means_pair(region, score, rows, pd.Series(FREQUENCY))

    assert result["method"] == "Welch ANOVA"
    assert all("effective_base" in group for group in result["groups"])
    pairs = {(item["a"], item["b"]): item for item in result["posthoc"]}
    assert pairs[("1", "3")]["significant"] and pairs[("2", "3")]["significant"]


def test_numeric_correlation_is_weighted_when_the_report_is(tmp_path: Path) -> None:
    project, frame = _project(tmp_path / "survey.sav")
    first = Variable(label="Оценка", kind="numeric", series=frame["SCORE"])
    second = Variable(label="Доход", kind="numeric", series=frame["INCOME"])
    rows = pd.Series(True, index=frame.index)
    weights = pd.Series(np.where(frame["SEX"] == 1, 1.5, 0.5), index=frame.index)

    result = numeric_correlation(first, second, rows, weights)

    reference, _, effective = weighted_correlation(
        frame["SCORE"].to_numpy(), frame["INCOME"].to_numpy(), weights.to_numpy()
    )
    assert result["statistic"] == pytest.approx(reference)
    assert result["effective_base"] == pytest.approx(effective)


def test_cards_use_the_report_weight(tmp_path: Path) -> None:
    project, _ = _project(tmp_path / "survey.sav")
    for question in project["configuration"]["questions"]:
        if question["code"] == "SCORE":
            question["role"] = "weight"
    project["configuration"]["report_settings"] = {"weight_variable": "SCORE"}
    card = {"id": "1", "a": {"kind": "question", "ref": "REGION"}}
    cards = [{**card, "b": {"kind": "question", "ref": "INCOME"}}]

    (result,) = analyse_cards(tmp_path / "survey.sav", project, cards)

    assert result["performed"] and result["method"] == "Welch ANOVA"
    assert result["weighted"] and "эффективной базе" in result["note"]
    assert all(group["effective_base"] < group["n"] for group in result["groups"])


def test_unusable_report_weight_fails_the_card_instead_of_ignoring_it(tmp_path: Path) -> None:
    """Вес без роли «Вес» не применяется — и карточка не считает молча без него."""
    project, _ = _project(tmp_path / "survey.sav")
    project["configuration"]["report_settings"] = {"weight_variable": "INCOME"}
    card = {"id": "1", "a": {"kind": "question", "ref": "SEX"}}

    (result,) = analyse_cards(
        tmp_path / "survey.sav", project, [{**card, "b": {"kind": "question", "ref": "REGION"}}]
    )

    assert not result["performed"]
    assert "Вес отчёта непригоден" in result["reason"]
