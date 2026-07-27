from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pandas as pd
import pyreadstat
import pytest

from sav_analytics.core.report import ReportError, build_statistics_txt, build_topline_xlsx
from sav_analytics.core.sav_reader import inspect_sav
from tests.test_sav_reader import write_fixture


def test_topline_workbook_has_required_sheets_and_numeric_cells(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    inspection = inspect_sav(source).to_dict()
    project = {
        "name": "Тест",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "banners": [],
            "filters": [],
            "report_filter_id": None,
        },
    }

    content = build_topline_xlsx(source, project)

    assert content.startswith(b"PK")
    with ZipFile(BytesIO(content)) as archive:
        workbook_xml = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        names = [
            item.attrib["name"]
            for item in workbook_xml.findall("m:sheets/m:sheet", namespace)
        ]
        assert names == ["topline_main", "topline_filter", "Содержание"]
        main_xml = archive.read("xl/worksheets/sheet1.xml")
        assert b'<c r="B4" s=' in main_xml
        assert b'<v>4</v>' in main_xml


def test_topline_applies_report_filter_to_total_base(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    inspection = inspect_sav(source).to_dict()
    report_filter = {
        "id": "men",
        "name": "Мужчины",
        "rule": {
            "kind": "group",
            "operator": "and",
            "items": [
                {
                    "kind": "condition",
                    "source": {"kind": "question", "ref": "Q1"},
                    "operator": "eq",
                    "values": [1],
                }
            ],
        },
    }
    project = {
        "name": "Фильтр",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "banners": [],
            "filters": [report_filter],
            "report_filter_id": "men",
        },
    }

    content = build_topline_xlsx(source, project)

    with ZipFile(BytesIO(content)) as archive:
        main_xml = archive.read("xl/worksheets/sheet1.xml")
        assert b'<c r="B4" s=' in main_xml
        assert b'<v>2</v>' in main_xml


def test_topline_marks_significant_subgroup_vs_rest_cells(tmp_path: Path) -> None:
    source = tmp_path / "statistics.sav"
    frame = pd.DataFrame(
        {
            "GROUP": [1] * 60 + [2] * 40,
            "OUTCOME": [1] * 42 + [2] * 18 + [1] * 20 + [2] * 20,
            "SCORE": [10 + index % 3 for index in range(60)]
            + [5 + index % 3 for index in range(40)],
        }
    )
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"GROUP": "Группа", "OUTCOME": "Результат", "SCORE": "Оценка"},
        variable_value_labels={
            "GROUP": {1: "Первая", 2: "Вторая"},
            "OUTCOME": {1: "Да", 2: "Нет"},
        },
        variable_measure={"GROUP": "nominal", "OUTCOME": "nominal", "SCORE": "scale"},
    )
    inspection = inspect_sav(source).to_dict()
    project = {
        "name": "Значимость",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "filters": [],
            "report_filter_id": None,
            "banners": [
                {
                    "name": "Основной",
                    "confidence_level": 0.95,
                    "bonferroni": False,
                    "minimum_base": 30,
                    "blocks": [
                        {
                            "label": "Группа",
                            "sources": [{"kind": "question", "ref": "GROUP"}],
                            "compare_to_total": True,
                            "compare_pairwise": True,
                        }
                    ],
                }
            ],
        },
    }

    content = build_topline_xlsx(source, project)

    assert _cell_fill(content, "Да", "C") == "FFD9EAD3"
    assert _cell_fill(content, "Да", "D") == "FFF4CCCC"
    comments = _comments(content)
    assert "Значимо выше: C — Вторая" in comments
    assert "Значимо ниже: B — Первая" in comments

    audit = build_statistics_txt(source, project)
    assert "СТАТИСТИЧЕСКИЙ АУДИТ ТОПЛАЙНА" in audit
    assert "Subgroup/Rest: B — Первая vs Rest(B)" in audit
    assert "Pairwise: B — Первая vs C — Вторая" in audit
    assert "Метод: z-test" in audit
    assert "Ожидаемые частоты 2×2" in audit
    assert "Метод: Welch t-test" in audit
    assert "Стандартные ошибки" in audit
    assert "p-value=" in audit
    assert "Скорректированный alpha: 0.050000" in audit


def test_topline_marks_small_subgroup_base_gray(tmp_path: Path) -> None:
    source = tmp_path / "small_base.sav"
    frame = pd.DataFrame({"GROUP": [1] * 20 + [2] * 40, "OUTCOME": [1] * 60})
    pyreadstat.write_sav(
        frame,
        source,
        variable_value_labels={"GROUP": {1: "Малая", 2: "Большая"}, "OUTCOME": {1: "Да"}},
        variable_measure={"GROUP": "nominal", "OUTCOME": "nominal"},
    )
    inspection = inspect_sav(source).to_dict()
    project = {
        "name": "Малая база",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "filters": [],
            "report_filter_id": None,
            "banners": [
                {
                    "name": "Основной",
                    "confidence_level": 0.95,
                    "bonferroni": False,
                    "minimum_base": 30,
                    "blocks": [
                        {
                            "label": "Группа",
                            "sources": [{"kind": "question", "ref": "GROUP"}],
                            "compare_to_total": True,
                            "compare_pairwise": False,
                        }
                    ],
                }
            ],
        },
    }

    content = build_topline_xlsx(source, project)

    assert _cell_fill(content, "Да", "C") == "FFD9D9D9"
    audit = build_statistics_txt(source, project)
    assert "Статус: пропущен" in audit
    assert "Невзвешенная база одной из групп ниже установленного порога" in audit


def test_topline_applies_ready_weight_and_audits_effective_bases(tmp_path: Path) -> None:
    source = tmp_path / "weighted.sav"
    frame = pd.DataFrame(
        {
            "GROUP": [1] * 40 + [2] * 40,
            "OUTCOME": [1] * 20 + [2] * 20 + [1] * 12 + [2] * 28,
            "W": [2.0] * 20 + [1.0] * 60,
        }
    )
    pyreadstat.write_sav(
        frame,
        source,
        variable_value_labels={
            "GROUP": {1: "Первая", 2: "Вторая"},
            "OUTCOME": {1: "Да", 2: "Нет"},
        },
        variable_measure={"GROUP": "nominal", "OUTCOME": "nominal", "W": "scale"},
    )
    inspection = inspect_sav(source).to_dict()
    project = {
        "name": "Вес",
        "original_filename": "weighted.sav",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "filters": [],
            "report_filter_id": None,
            "banners": [
                {
                    "name": "Основной",
                    "confidence_level": 0.95,
                    "bonferroni": False,
                    "minimum_base": 30,
                    "weight_variable": "W",
                    "blocks": [
                        {
                            "label": "Группа",
                            "sources": [{"kind": "question", "ref": "GROUP"}],
                            "compare_to_total": True,
                            "compare_pairwise": False,
                        }
                    ],
                }
            ],
        },
    }

    content = build_topline_xlsx(source, project)
    audit = build_statistics_txt(source, project)

    assert _cell_value(content, "Да", "B") == pytest.approx(52.0)
    assert "Вес: W" in audit
    assert "Эффективные базы: n_eff1=36.000000; n_eff2=40.000000" in audit
    assert "Характер теста: приближённый" in audit


def test_topline_rejects_invalid_ready_weight(tmp_path: Path) -> None:
    source = tmp_path / "invalid_weight.sav"
    frame = pd.DataFrame({"GROUP": [1, 2], "W": [1.0, 0.0]})
    pyreadstat.write_sav(frame, source, variable_value_labels={"GROUP": {1: "A", 2: "B"}})
    inspection = inspect_sav(source).to_dict()
    project = {
        "name": "Ошибка веса",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "filters": [],
            "report_filter_id": None,
            "banners": [
                {
                    "name": "Основной",
                    "weight_variable": "W",
                    "blocks": [
                        {
                            "sources": [{"kind": "question", "ref": "GROUP"}],
                            "compare_to_total": False,
                            "compare_pairwise": False,
                        }
                    ],
                }
            ],
        },
    }

    with pytest.raises(ReportError, match="положительными"):
        build_topline_xlsx(source, project)


def test_topline_applies_calculated_raking_weight(tmp_path: Path) -> None:
    source = tmp_path / "raking.sav"
    frame = pd.DataFrame({"GROUP": [1] * 20 + [2] * 20})
    pyreadstat.write_sav(
        frame,
        source,
        variable_value_labels={"GROUP": {1: "A", 2: "B"}},
        variable_measure={"GROUP": "nominal"},
    )
    inspection = inspect_sav(source).to_dict()
    weight_id = "weight-1"
    project = {
        "name": "Raking",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "filters": [],
            "report_filter_id": None,
            "calculated_weights": [
                {
                    "id": weight_id,
                    "name": "Целевой вес",
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
            ],
            "banners": [
                {
                    "name": "Основной",
                    "calculated_weight_id": weight_id,
                    "blocks": [
                        {
                            "sources": [{"kind": "question", "ref": "GROUP"}],
                            "compare_to_total": False,
                            "compare_pairwise": False,
                        }
                    ],
                }
            ],
        },
    }

    content = build_topline_xlsx(source, project)
    audit = build_statistics_txt(source, project)

    assert _cell_value(content, "A", "B") == pytest.approx(70)
    assert "Вес: Целевой вес (raking/IPF)" in audit


def _cell_fill(content: bytes, row_label: str, column: str) -> str | None:
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(content)) as archive:
        shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = ["".join(item.itertext()) for item in shared_root.findall("m:si", namespace)]
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        target_row = None
        for cell in sheet.findall(".//m:c", namespace):
            if not cell.attrib["r"].startswith("A") or cell.attrib.get("t") != "s":
                continue
            value = cell.find("m:v", namespace)
            if value is not None and shared[int(value.text)] == row_label:
                target_row = cell.attrib["r"][1:]
                break
        assert target_row is not None
        target = next(
            cell
            for cell in sheet.findall(".//m:c", namespace)
            if cell.attrib["r"] == f"{column}{target_row}"
        )
        style_index = int(target.attrib["s"])
        styles = ElementTree.fromstring(archive.read("xl/styles.xml"))
        cell_formats = styles.find("m:cellXfs", namespace)
        fills = styles.find("m:fills", namespace)
        fill_index = int(cell_formats[style_index].attrib["fillId"])
        color = fills[fill_index].find("m:patternFill/m:fgColor", namespace)
        return color.attrib.get("rgb") if color is not None else None


def _comments(content: bytes) -> str:
    with ZipFile(BytesIO(content)) as archive:
        root = ElementTree.fromstring(archive.read("xl/comments1.xml"))
        return "\n".join(root.itertext())


def _cell_value(content: bytes, row_label: str, column: str) -> float:
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(content)) as archive:
        shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = ["".join(item.itertext()) for item in shared_root.findall("m:si", namespace)]
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        target_row = next(
            cell.attrib["r"][1:]
            for cell in sheet.findall(".//m:c", namespace)
            if cell.attrib["r"].startswith("A")
            and cell.attrib.get("t") == "s"
            and shared[int(cell.find("m:v", namespace).text)] == row_label
        )
        target = next(
            cell
            for cell in sheet.findall(".//m:c", namespace)
            if cell.attrib["r"] == f"{column}{target_row}"
        )
        return float(target.find("m:v", namespace).text)
