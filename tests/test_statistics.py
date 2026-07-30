import math

import pytest

from sav_analytics.core.statistics import (
    balance_z_test,
    effective_sample_size,
    proportion_z_test,
    subgroup_vs_rest_z_test,
    weighted_proportion_z_test,
    weighted_welch_t_test,
    welch_t_test,
)


def test_nps_balance_z_test_matches_reference_formula() -> None:
    group_a = [-1] * 10 + [0] * 20 + [1] * 70
    group_b = [-1] * 30 + [0] * 30 + [1] * 40

    result = balance_z_test(group_a, group_b, method="NPS z-test")

    assert result.performed is True
    assert result.group_estimates == pytest.approx((0.6, 0.1))
    assert result.group_variances == pytest.approx((0.44, 0.69))
    assert result.difference == pytest.approx(0.5)
    assert result.statistic == pytest.approx(4.703604341054247)
    assert result.significant is True
    assert result.direction == "higher"


def test_weighted_balance_uses_kish_effective_base() -> None:
    scores_a = [-1] * 20 + [1] * 20
    scores_b = [-1] * 20 + [1] * 20
    result = balance_z_test(
        scores_a,
        scores_b,
        weights_a=[2.0] * 20 + [1.0] * 20,
        weights_b=[1.0] * 40,
        minimum_base=30,
    )

    assert result.approximate is True
    assert result.effective_bases == pytest.approx((36.0, 40.0))
    assert result.group_estimates == pytest.approx((-1 / 3, 0.0))


def test_proportion_z_test_matches_reference_result() -> None:
    result = proportion_z_test(70, 100, 50, 100)

    assert result.performed is True
    assert result.method == "z-test"
    assert result.statistic == pytest.approx(2.886751345948128)
    assert result.p_value == pytest.approx(0.003892417122778628)
    assert result.difference == pytest.approx(0.2)
    assert result.confidence_interval == pytest.approx(
        (0.06706877501808317, 0.33293122498191674)
    )
    assert result.significant is True
    assert result.direction == "higher"
    assert result.expected_frequencies == pytest.approx((60, 40, 60, 40))


def test_proportion_z_test_skips_small_expected_frequency_without_fallback() -> None:
    result = proportion_z_test(1, 30, 8, 30)

    assert result.performed is False
    assert result.p_value is None
    assert result.reason == "Хотя бы одна ожидаемая частота таблицы 2×2 меньше 5."
    assert result.expected_frequencies == pytest.approx((4.5, 25.5, 4.5, 25.5))


def test_proportion_z_test_applies_bonferroni_to_decision_and_interval() -> None:
    unadjusted = proportion_z_test(63, 100, 49, 100)
    adjusted = proportion_z_test(63, 100, 49, 100, comparisons=4)

    assert unadjusted.p_value == adjusted.p_value
    assert unadjusted.significant is True
    assert adjusted.alpha == pytest.approx(0.0125)
    assert adjusted.significant is False
    assert adjusted.confidence_interval[0] < unadjusted.confidence_interval[0]
    assert adjusted.confidence_interval[1] > unadjusted.confidence_interval[1]


def test_proportion_z_test_uses_project_minimum_base() -> None:
    result = proportion_z_test(18, 29, 12, 29)

    assert result.performed is False
    assert "база" in result.reason.lower()


def test_subgroup_is_compared_with_non_overlapping_rest() -> None:
    outcome = [True] * 42 + [False] * 18 + [True] * 20 + [False] * 20
    total = [True] * 100
    subgroup = [True] * 60 + [False] * 40

    result = subgroup_vs_rest_z_test(outcome, total, subgroup)

    assert result.performed is True
    assert result.group_bases == (60, 40)
    assert result.group_successes == (42, 20)
    assert result.group_estimates == pytest.approx((0.7, 0.5))
    assert result.difference == pytest.approx(0.2)


def test_subgroup_outside_total_is_rejected() -> None:
    with pytest.raises(ValueError, match="входить в Total"):
        subgroup_vs_rest_z_test(
            [True, False],
            [True, False],
            [False, True],
            minimum_base=1,
        )


def test_welch_t_test_matches_reference_calculation() -> None:
    group_a = [10 + index % 7 for index in range(42)]
    group_b = [8 + index % 5 for index in range(38)]

    result = welch_t_test(group_a, group_b)

    assert result.performed is True
    assert result.method == "Welch t-test"
    assert result.statistic == pytest.approx(7.930393205635306)
    assert result.degrees_of_freedom == pytest.approx(73.63388322026942)
    assert result.p_value == pytest.approx(1.806579313114011e-11)
    assert result.difference == pytest.approx(3.078947368421053)
    assert result.confidence_interval == pytest.approx(
        (2.3052854231025406, 3.8526093137395656)
    )
    assert result.significant is True
    assert result.direction == "higher"


def test_welch_t_test_drops_missing_values_and_enforces_minimum_base() -> None:
    result = welch_t_test([1.0] * 29 + [math.nan], [2.0] * 30)

    assert result.performed is False
    assert "база" in result.reason.lower()


def test_effective_sample_size_uses_kish_formula() -> None:
    assert effective_sample_size([1, 1, 2, 2]) == pytest.approx(3.6)


def test_weighted_proportion_test_uses_weighted_estimates_and_effective_bases() -> None:
    result = weighted_proportion_z_test(
        [True] * 20 + [False] * 20,
        [2.0] * 20 + [1.0] * 20,
        [True] * 12 + [False] * 28,
        [1.0] * 40,
    )

    assert result.approximate is True
    assert result.group_estimates == pytest.approx((2 / 3, 0.3))
    assert result.effective_bases == pytest.approx((36.0, 40.0))
    assert result.group_bases == (40, 40)
    assert result.performed is True


def test_weighted_welch_with_uniform_weights_matches_unweighted_result() -> None:
    group_a = [10 + index % 7 for index in range(42)]
    group_b = [8 + index % 5 for index in range(38)]

    unweighted = welch_t_test(group_a, group_b)
    weighted = weighted_welch_t_test(
        group_a, [1.0] * len(group_a), group_b, [1.0] * len(group_b)
    )

    assert weighted.approximate is True
    assert weighted.effective_bases == pytest.approx((42, 38))
    assert weighted.statistic == pytest.approx(unweighted.statistic)
    assert weighted.p_value == pytest.approx(unweighted.p_value)
