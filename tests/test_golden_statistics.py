import json
from pathlib import Path

import pytest

from sav_analytics.core.statistics import proportion_z_test, welch_t_test

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
