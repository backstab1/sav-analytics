from pathlib import Path

import pytest

from sav_analytics.core.recoding import (
    RecodingError,
    calculate_recode_preview,
    validate_numeric_recode,
)
from sav_analytics.core.sav_reader import inspect_sav
from tests.test_sav_reader import write_fixture


def definition() -> dict:
    return {
        "code": "SCORE_GROUP",
        "name": "Группы оценки",
        "source_variable": "Q2",
        "categories": [
            {"label": "Низкая", "lower": 0, "upper": 7},
            {"label": "Высокая", "lower": 8, "upper": 10},
        ],
    }


def test_numeric_recode_preview_counts_ranges_and_missing(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    variables = inspect_sav(source).to_dict()["variables"]

    validate_numeric_recode(definition(), variables)
    preview = calculate_recode_preview(source, definition())

    assert [row["count"] for row in preview["rows"]] == [1, 2]
    assert preview["source_missing_count"] == 1
    assert preview["out_of_range_count"] == 0


def test_numeric_recode_rejects_overlapping_ranges(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    variables = inspect_sav(source).to_dict()["variables"]
    overlapping = definition()
    overlapping["categories"][1]["lower"] = 7

    with pytest.raises(RecodingError, match="пересекаются"):
        validate_numeric_recode(overlapping, variables)

