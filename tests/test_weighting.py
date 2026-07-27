import pandas as pd
import pytest

from sav_analytics.core.weighting import WeightingError, calculate_raking


def test_raking_matches_two_target_distributions() -> None:
    frame = pd.DataFrame(
        {
            "SEX": [1] * 60 + [2] * 40,
            "AGE": [1] * 30 + [2] * 30 + [1] * 20 + [2] * 20,
        }
    )
    definition = {
        "dimensions": [
            {
                "variable": "SEX",
                "label": "Пол",
                "targets": [
                    {"label": "Мужчины", "values": [1], "percent": 50},
                    {"label": "Женщины", "values": [2], "percent": 50},
                ],
            },
            {
                "variable": "AGE",
                "label": "Возраст",
                "targets": [
                    {"label": "Младше", "values": [1], "percent": 40},
                    {"label": "Старше", "values": [2], "percent": 60},
                ],
            },
        ],
        "lower_bound": None,
        "upper_bound": None,
    }

    result = calculate_raking(frame, definition)

    assert result.weights.mean() == pytest.approx(1)
    assert result.maximum_deviation < 0.001
    diagnostics = result.diagnostics
    assert diagnostics["effective_base"] <= len(frame)
    assert diagnostics["design_effect"] >= 1
    assert diagnostics["distributions"][0]["categories"][0][
        "after_percent"
    ] == pytest.approx(50, abs=0.1)
    assert diagnostics["distributions"][1]["categories"][0][
        "after_percent"
    ] == pytest.approx(40, abs=0.1)


def test_raking_normalizes_target_sum_within_tolerance() -> None:
    frame = pd.DataFrame({"GROUP": [1] * 50 + [2] * 50})
    result = calculate_raking(
        frame,
        {
            "dimensions": [
                {
                    "variable": "GROUP",
                    "targets": [
                        {"label": "A", "values": [1], "percent": 49.95},
                        {"label": "B", "values": [2], "percent": 49.95},
                    ],
                }
            ]
        },
    )

    assert result.weights.mean() == pytest.approx(1)


@pytest.mark.parametrize(
    ("frame", "definition", "message"),
    [
        (
            pd.DataFrame({"GROUP": [1, 2]}),
            {
                "dimensions": [
                    {
                        "variable": "GROUP",
                        "targets": [
                            {"label": "A", "values": [1], "percent": 70},
                            {"label": "B", "values": [2], "percent": 20},
                        ],
                    }
                ]
            },
            "100%",
        ),
        (
            pd.DataFrame({"GROUP": [1, None]}),
            {
                "dimensions": [
                    {
                        "variable": "GROUP",
                        "targets": [
                            {"label": "A", "values": [1], "percent": 50},
                            {"label": "B", "values": [2], "percent": 50},
                        ],
                    }
                ]
            },
            "пропуски",
        ),
        (
            pd.DataFrame({"GROUP": [1, 1]}),
            {
                "dimensions": [
                    {
                        "variable": "GROUP",
                        "targets": [
                            {"label": "A", "values": [1], "percent": 50},
                            {"label": "B", "values": [2], "percent": 50},
                        ],
                    }
                ]
            },
            "отсутствует",
        ),
    ],
)
def test_raking_rejects_invalid_targets(frame, definition, message) -> None:
    with pytest.raises(WeightingError, match=message):
        calculate_raking(frame, definition)
