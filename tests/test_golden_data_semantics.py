import json
from pathlib import Path

import pandas as pd
import pyreadstat
import pytest

from sav_analytics.core.filtering import calculate_filter_preview
from sav_analytics.core.report import build_topline_xlsx
from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.core.topline import calculate_preview
from tests.test_report import _cell_value

GOLDEN_DIR = Path(__file__).parent / "golden"


def _reference() -> dict:
    return json.loads(
        (GOLDEN_DIR / "data_semantics_reference.json").read_text(encoding="utf-8")
    )


def _multiple_filter(operator: str) -> dict:
    return {
        "name": operator,
        "rule": {
            "kind": "group",
            "operator": "and",
            "items": [
                {
                    "kind": "condition",
                    "source": {"kind": "question", "ref": "MR"},
                    "operator": operator,
                    "values": ["MR_1"],
                }
            ],
        },
    }


def test_counted_value_two_is_consistent_in_preview_filter_and_report(tmp_path: Path) -> None:
    case = _reference()["multiple_response_counted_value"]
    source = tmp_path / "counted_value_2.sav"
    frame = pd.DataFrame(case["rows"], columns=["MR_1", "MR_2"])
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"MR_1": "Марки: Альфа", "MR_2": "Марки: Бета"},
        variable_value_labels={
            "MR_1": {1: "Не выбрано", 2: "Выбрано"},
            "MR_2": {1: "Не выбрано", 2: "Выбрано"},
        },
        variable_measure={"MR_1": "nominal", "MR_2": "nominal"},
    )
    inspection = inspect_sav(source).to_dict()
    question = {
        "code": "MR",
        "label": "Марки",
        "question_type": "multiple_choice_dichotomy",
        "role": "question",
        "source_variables": ["MR_1", "MR_2"],
        "valid_count": case["valid_base"],
        "missing_count": 1,
        "included_in_report": True,
        "special_items": [],
        "multiple_response": {
            "encoding": "dichotomy",
            "counted_value": case["counted_value"],
        },
    }
    inspection["questions"] = [question]
    project = {
        "name": "MR golden",
        "inspection": inspection,
        "configuration": {
            "questions": [question],
            "recodings": [],
            "filters": [],
            "banners": [],
            "report_filter_id": None,
        },
    }

    preview = calculate_preview(source, question, inspection["variables"])
    selected = calculate_filter_preview(source, _multiple_filter("selected"), project)
    selected_none = calculate_filter_preview(
        source, _multiple_filter("selected_none"), project
    )
    workbook = build_topline_xlsx(source, project)

    assert preview["valid_base"] == case["valid_base"]
    assert [row["count"] for row in preview["rows"]] == case["selected_counts"]
    assert [row["percent_filter"] for row in preview["rows"]] == pytest.approx(
        case["selected_percent_valid"]
    )
    assert selected["selected"] == case["selected_first_filter_count"]
    assert selected_none["selected"] == case["selected_none_first_filter_count"]
    assert _cell_value(workbook, "Марки: Альфа", "B") == pytest.approx(
        case["topline_main_first_percent"]
    )


def test_spss_user_missing_is_excluded_from_nps_preview_and_report(tmp_path: Path) -> None:
    case = _reference()["spss_user_missing_nps"]
    source = tmp_path / "nps_user_missing.sav"
    pyreadstat.write_sav(
        pd.DataFrame({"NPS": case["values"]}),
        source,
        variable_value_labels={
            "NPS": {0: "0", 7: "7", 10: "10", 99: "Затрудняюсь ответить"}
        },
        missing_ranges={"NPS": [case["user_missing"]]},
        variable_measure={"NPS": "scale"},
    )
    inspection = inspect_sav(source).to_dict()
    question = inspection["questions"][0]
    question["special_metric"] = "nps"
    project = {
        "name": "NPS user-missing golden",
        "inspection": inspection,
        "configuration": {
            "questions": [question],
            "recodings": [],
            "filters": [],
            "banners": [],
            "report_filter_id": None,
        },
    }

    preview = calculate_preview(source, question, inspection["variables"])
    workbook = build_topline_xlsx(source, project)

    assert question["valid_count"] == case["valid_base"]
    assert question["missing_count"] == case["missing_base"]
    assert preview["valid_base"] == case["valid_base"]
    missing_row = next(row for row in preview["rows"] if row["value"] == 99.0)
    assert missing_row["count"] == 0
    assert _cell_value(workbook, "NPS", "B") == pytest.approx(case["nps"])
