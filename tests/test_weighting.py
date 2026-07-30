from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pandas as pd
import pyreadstat
import pytest

from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.core.weighting import (
    WeightingError,
    build_raking_export,
    calculate_raking,
)


def test_raking_export_contains_id_source_and_calculated_weights(tmp_path: Path) -> None:
    source = tmp_path / "weights.sav"
    frame = pd.DataFrame(
        {"RESP_ID": [101, 102, 103, 104], "GROUP": [1, 1, 2, 2], "W": [2, 2, 1, 1]}
    )
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"RESP_ID": "ID респондента", "W": "Исходный вес"},
        variable_value_labels={"GROUP": {1: "A", 2: "B"}},
        variable_measure={"RESP_ID": "nominal", "GROUP": "nominal", "W": "scale"},
    )
    inspection = inspect_sav(source).to_dict()
    questions = inspection["questions"]
    next(item for item in questions if item["code"] == "RESP_ID")["role"] = "id"
    next(item for item in questions if item["code"] == "W")["role"] = "weight"
    project = {"inspection": inspection, "configuration": {"questions": questions}}
    definition = {
        "name": "Вес 70/30",
        "dimensions": [
            {
                "variable": "GROUP",
                "label": "Группа",
                "targets": [
                    {"label": "A", "values": [1], "percent": 70},
                    {"label": "B", "values": [2], "percent": 30},
                ],
            }
        ],
        "lower_bound": None,
        "upper_bound": None,
    }

    content = build_raking_export(source, definition, project)

    assert content.startswith(b"PK")
    with ZipFile(BytesIO(content)) as archive:
        shared_strings = archive.read("xl/sharedStrings.xml")
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        values = {
            cell.attrib["r"]: float(cell.find("m:v", namespace).text)
            for cell in sheet.findall(".//m:c", namespace)
            if cell.attrib.get("t") != "s" and cell.find("m:v", namespace) is not None
        }
        assert "ID респондента".encode() in shared_strings
        assert "Исходный вес (W)".encode() in shared_strings
        assert "Рассчитанный вес".encode() in shared_strings
        assert values["A2"] == 101
        assert values["B2"] == 2
        assert values["C2"] == pytest.approx(1.4)
        assert values["C4"] == pytest.approx(0.6)

    next(item for item in questions if item["code"] == "RESP_ID")["role"] = "question"
    fallback = build_raking_export(source, definition, project)
    with ZipFile(BytesIO(fallback)) as archive:
        assert "Номер строки".encode() in archive.read("xl/sharedStrings.xml")
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        first_id = next(
            cell
            for cell in sheet.findall(".//m:c", namespace)
            if cell.attrib["r"] == "A2"
        )
        assert float(first_id.find("m:v", namespace).text) == 1


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
