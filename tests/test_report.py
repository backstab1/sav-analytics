from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from sav_analytics.core.report import build_topline_xlsx
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
