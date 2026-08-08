from pathlib import Path

import pytest

from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.core.topline import calculate_preview
from tests.test_sav_reader import write_counted_value_fixture, write_grouped_fixture


def test_matrix_preview_excludes_special_value_from_mean(tmp_path: Path) -> None:
    source = tmp_path / "grouped.sav"
    write_grouped_fixture(source)
    inspection = inspect_sav(source).to_dict()
    matrix = next(item for item in inspection["questions"] if item["code"] == "Q5")

    preview = calculate_preview(source, matrix, inspection["variables"])

    assert len(preview["items"]) == 2
    assert preview["items"][0]["statistics"]["mean"] == pytest.approx(5 / 3)
    special_row = next(
        row for row in preview["items"][0]["rows"] if row["value"] == 99.0
    )
    assert special_row["is_special"]


def test_multiple_preview_warns_about_special_answer_conflict(tmp_path: Path) -> None:
    source = tmp_path / "grouped.sav"
    write_grouped_fixture(source)
    inspection = inspect_sav(source).to_dict()
    multiple = next(item for item in inspection["questions"] if item["code"] == "Q6")

    preview = calculate_preview(source, multiple, inspection["variables"])

    assert any("1 респондентов" in warning for warning in preview["warnings"])


def test_multiple_preview_uses_configured_counted_value(tmp_path: Path) -> None:
    source = tmp_path / "counted_value.sav"
    write_counted_value_fixture(source)
    inspection = inspect_sav(source).to_dict()
    multiple = {
        "code": "MR",
        "label": "Марки",
        "question_type": "multiple_choice_dichotomy",
        "source_variables": ["MR_1", "MR_2"],
        "special_items": [],
        "multiple_response": {"encoding": "dichotomy", "counted_value": 2},
    }

    preview = calculate_preview(source, multiple, inspection["variables"])

    assert preview["valid_base"] == 3
    assert [row["count"] for row in preview["rows"]] == [2, 2]
    assert [row["percent_filter"] for row in preview["rows"]] == pytest.approx(
        [2 / 3, 2 / 3]
    )
    assert any("код 2" in warning for warning in preview["warnings"])
