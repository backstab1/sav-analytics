"""Разбиение числовой переменной на диапазоны (PQ.9)."""

from pathlib import Path

import pandas as pd
import pyreadstat
import pytest

from sav_analytics.core.recoding import (
    RecodingError,
    calculate_recode_preview,
    suggest_ranges,
    validate_recode,
)


def _variable(path: Path, values: list[float]) -> dict:
    pyreadstat.write_sav(pd.DataFrame({"X": values}), path)
    return {"name": "X", "storage_type": "numeric", "value_labels": []}


def _as_recoding(suggestion: dict) -> dict:
    return {
        "mode": "ranges",
        "code": "XG",
        "name": "Группы",
        "source_variable": "X",
        "categories": [
            {key: item[key] for key in ("label", "lower", "upper")}
            for item in suggestion["categories"]
        ],
    }


def test_quantiles_split_integers_into_equal_groups(tmp_path: Path) -> None:
    source = tmp_path / "ages.sav"
    variable = _variable(source, [float(value) for value in range(18, 58)])

    suggestion = suggest_ranges(source, variable, "quantiles", 4)

    assert [item["count"] for item in suggestion["categories"]] == [10, 10, 10, 10]
    assert suggestion["categories"][0]["label"] == "до 27"
    assert suggestion["categories"][1]["lower"] == 28
    assert suggestion["categories"][-1]["upper"] is None
    recoding = _as_recoding(suggestion)
    validate_recode(recoding, [variable])
    preview = calculate_recode_preview(source, recoding)
    assert preview["out_of_range_count"] == 0


def test_equal_intervals_follow_the_data_precision(tmp_path: Path) -> None:
    source = tmp_path / "scores.sav"
    variable = _variable(source, [0.0, 0.5, 1.2, 2.5, 3.7, 4.9, 5.0, 7.5, 9.9, 10.0])

    suggestion = suggest_ranges(source, variable, "equal", 2)

    assert suggestion["decimals"] == 1
    assert [item["label"] for item in suggestion["categories"]] == ["до 5,0", "5,1 и больше"]
    assert sum(item["count"] for item in suggestion["categories"]) == 10
    recoding = _as_recoding(suggestion)
    validate_recode(recoding, [variable])
    assert calculate_recode_preview(source, recoding)["out_of_range_count"] == 0


def test_ties_collapse_quantile_groups(tmp_path: Path) -> None:
    source = tmp_path / "ties.sav"
    variable = _variable(source, [1.0] * 90 + [2.0] * 10)

    suggestion = suggest_ranges(source, variable, "quantiles", 4)

    assert len(suggestion["categories"]) == 2
    assert [item["count"] for item in suggestion["categories"]] == [90, 10]


def test_constant_or_text_variable_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "flat.sav"
    variable = _variable(source, [3.0] * 5)
    with pytest.raises(RecodingError):
        suggest_ranges(source, variable, "quantiles", 3)
    with pytest.raises(RecodingError):
        suggest_ranges(source, {**variable, "storage_type": "string"}, "equal", 3)
