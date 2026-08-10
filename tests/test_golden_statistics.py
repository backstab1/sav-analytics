import json
from pathlib import Path

import pytest

from sav_analytics.core.statistics import (
    balance_z_test,
    proportion_z_test,
    weighted_proportion_z_test,
    weighted_welch_t_test,
    welch_t_test,
)

GOLDEN_DIR = Path(__file__).parent / "golden"


def _reference() -> dict:
    return json.loads((GOLDEN_DIR / "statistics_reference.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _reference()["proportion_cases"], ids=lambda case: case["id"])
def test_proportion_results_match_independent_base_r_reference(case: dict) -> None:
    result = proportion_z_test(
        case["successes"][0],
        case["bases"][0],
        case["successes"][1],
        case["bases"][1],
        comparisons=case["comparisons"],
    )

    assert result.performed is case["performed"]
    assert result.difference == pytest.approx(case["difference"])
    assert result.alpha == pytest.approx(case["alpha"])
    assert result.expected_frequencies == pytest.approx(case["expected_frequencies"])
    if case["performed"]:
        assert result.statistic == pytest.approx(case["statistic"])
        assert result.p_value == pytest.approx(case["p_value"])
        assert result.confidence_interval == pytest.approx(case["confidence_interval"])
    else:
        assert result.statistic is None
        assert result.p_value is None
        assert result.confidence_interval is None
        assert "меньше 5" in result.reason


def test_welch_result_matches_independent_base_r_reference() -> None:
    case = _reference()["welch_case"]
    result = welch_t_test(case["group_a"], case["group_b"])

    assert result.performed is True
    assert result.statistic == pytest.approx(case["statistic"])
    assert result.degrees_of_freedom == pytest.approx(case["degrees_of_freedom"])
    assert result.p_value == pytest.approx(case["p_value"])
    assert result.difference == pytest.approx(case["difference"])
    assert result.confidence_interval == pytest.approx(case["confidence_interval"])


def test_weighted_proportion_matches_independent_base_r_reference() -> None:
    case = _reference()["weighted_proportion_case"]
    outcome_a = [True] * case["outcome_a"]["selected"] + [False] * case["outcome_a"][
        "not_selected"
    ]
    weights_a = [case["weights_a"]["selected"]] * case["outcome_a"]["selected"] + [
        case["weights_a"]["not_selected"]
    ] * case["outcome_a"]["not_selected"]
    outcome_b = [True] * case["outcome_b"]["selected"] + [False] * case["outcome_b"][
        "not_selected"
    ]
    weights_b = [case["weights_b"]["selected"]] * case["outcome_b"]["selected"] + [
        case["weights_b"]["not_selected"]
    ] * case["outcome_b"]["not_selected"]

    result = weighted_proportion_z_test(outcome_a, weights_a, outcome_b, weights_b)

    assert result.approximate is True
    assert result.statistic == pytest.approx(case["statistic"])
    assert result.p_value == pytest.approx(case["p_value"])
    assert result.difference == pytest.approx(case["difference"])
    assert result.confidence_interval == pytest.approx(case["confidence_interval"])
    assert result.group_estimates == pytest.approx(case["group_estimates"])
    assert result.effective_bases == pytest.approx(case["effective_bases"])
    assert result.group_weight_sums == pytest.approx(case["weight_sums"])


def test_weighted_welch_matches_independent_base_r_reference() -> None:
    reference = _reference()
    case = reference["weighted_welch_case"]
    values_a = reference["welch_case"]["group_a"]
    values_b = reference["welch_case"]["group_b"]
    weights_a = [case["weights_a"]["first_weight"]] * case["weights_a"][
        "first_count"
    ] + [case["weights_a"]["remaining_weight"]] * (
        len(values_a) - case["weights_a"]["first_count"]
    )
    weights_b = [case["weight_b"]] * len(values_b)

    result = weighted_welch_t_test(values_a, weights_a, values_b, weights_b)

    assert result.approximate is True
    assert result.statistic == pytest.approx(case["statistic"])
    assert result.degrees_of_freedom == pytest.approx(case["degrees_of_freedom"])
    assert result.p_value == pytest.approx(case["p_value"])
    assert result.difference == pytest.approx(case["difference"])
    assert result.confidence_interval == pytest.approx(case["confidence_interval"])
    assert result.group_estimates == pytest.approx(case["group_estimates"])
    assert result.group_variances == pytest.approx(case["group_variances"])
    assert result.effective_bases == pytest.approx(case["effective_bases"])


def test_nps_balance_matches_independent_base_r_reference() -> None:
    case = _reference()["nps_balance_case"]

    def scores(counts: dict) -> list[int]:
        return (
            [-1] * counts["detractor"]
            + [0] * counts["passive"]
            + [1] * counts["promoter"]
        )

    result = balance_z_test(
        scores(case["group_a_counts"]),
        scores(case["group_b_counts"]),
        method="NPS z-test",
    )

    assert result.statistic == pytest.approx(case["statistic"])
    assert result.p_value == pytest.approx(case["p_value"])
    assert result.difference == pytest.approx(case["difference"])
    assert result.confidence_interval == pytest.approx(case["confidence_interval"])
    assert result.group_estimates == pytest.approx(case["group_estimates"])
    assert result.group_variances == pytest.approx(case["group_variances"])


def _balance_scores(detractor: int, passive: int, promoter: int) -> list[int]:
    return [-1] * detractor + [0] * passive + [1] * promoter


def test_csat_balance_matches_independent_base_r_reference() -> None:
    """CSAT считается тем же критерием, что и NPS, но с другой разметкой баллов."""
    case = _reference()["csat_balance_case"]
    result = balance_z_test(
        _balance_scores(**{
            "detractor": case["group_a_counts"]["dissatisfied"],
            "passive": case["group_a_counts"]["neutral"],
            "promoter": case["group_a_counts"]["satisfied"],
        }),
        _balance_scores(**{
            "detractor": case["group_b_counts"]["dissatisfied"],
            "passive": case["group_b_counts"]["neutral"],
            "promoter": case["group_b_counts"]["satisfied"],
        }),
        method="CSAT balance z-test",
    )

    assert result.performed is True
    assert result.statistic == pytest.approx(case["statistic"])
    assert result.p_value == pytest.approx(case["p_value"])
    assert result.difference == pytest.approx(case["difference"])
    assert result.group_estimates == pytest.approx(tuple(case["group_estimates"]))
    assert result.group_variances == pytest.approx(tuple(case["group_variances"]))
    assert result.confidence_interval == pytest.approx(tuple(case["confidence_interval"]))


def test_weighted_balance_matches_independent_base_r_reference() -> None:
    """Взвешенный баланс: дисперсия делится на эффективную базу Киша."""
    case = _reference()["weighted_balance_case"]
    group_a, group_b = case["group_a"], case["group_b"]
    scores_a = _balance_scores(group_a["detractor"], group_a["passive"], group_a["promoter"])
    scores_b = _balance_scores(group_b["detractor"], group_b["passive"], group_b["promoter"])
    weights_a = [group_a["weight_first_30"]] * 30 + [group_a["weight_rest"]] * 70
    weights_b = [group_b["weight_first_60"]] * 60 + [group_b["weight_rest"]] * 40

    result = balance_z_test(
        scores_a,
        scores_b,
        weights_a=weights_a,
        weights_b=weights_b,
        method="NPS balance z-test",
    )

    assert result.performed is True
    assert result.approximate is True
    assert result.statistic == pytest.approx(case["statistic"])
    assert result.p_value == pytest.approx(case["p_value"])
    assert result.group_estimates == pytest.approx(tuple(case["group_estimates"]))
    assert result.group_variances == pytest.approx(tuple(case["group_variances"]))
    assert result.effective_bases == pytest.approx(tuple(case["effective_bases"]))
    assert result.confidence_interval == pytest.approx(tuple(case["confidence_interval"]))


def test_wave_proportion_comparison_matches_independent_base_r_reference() -> None:
    """Волны сравниваются как две независимые выборки обычным z-test."""
    case = next(item for item in _reference()["wave_cases"] if item["id"] == "wave_proportion")
    result = proportion_z_test(
        case["successes"][0],
        case["bases"][0],
        case["successes"][1],
        case["bases"][1],
        comparisons=case["comparisons"],
    )

    assert result.performed is True
    assert result.statistic == pytest.approx(case["statistic"])
    assert result.p_value == pytest.approx(case["p_value"])
    assert result.difference == pytest.approx(case["difference"])
    assert result.confidence_interval == pytest.approx(tuple(case["confidence_interval"]))
    assert result.expected_frequencies == pytest.approx(tuple(case["expected_frequencies"]))


def test_wave_mean_comparison_matches_independent_base_r_reference() -> None:
    """Средние между волнами сравниваются Welch t-test."""
    case = next(item for item in _reference()["wave_cases"] if item["id"] == "wave_welch")
    sample_a = [case["sample_a"]["offset"] + index % case["sample_a"]["cycle"]
                for index in range(case["sample_a"]["size"])]
    sample_b = [case["sample_b"]["offset"] + index % case["sample_b"]["cycle"]
                for index in range(case["sample_b"]["size"])]

    result = welch_t_test(sample_a, sample_b)

    assert result.performed is True
    assert result.statistic == pytest.approx(case["statistic"])
    assert result.degrees_of_freedom == pytest.approx(case["degrees_of_freedom"])
    assert result.p_value == pytest.approx(case["p_value"])
    assert result.difference == pytest.approx(case["difference"])
    assert result.confidence_interval == pytest.approx(tuple(case["confidence_interval"]))
