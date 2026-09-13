"""Живая таблица: одно число на экране и в книге (роадмап, PQ.3)."""

import copy
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.report import build_topline_xlsx
from sav_analytics.core.reporting.live import build_live_table
from sav_analytics.core.reporting.models import ReportError
from sav_analytics.repository import ProjectRepository
from tests.test_report import _significance_project
from tests.test_sav_reader import write_fixture

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _numeric_cells(content: bytes) -> dict[str, float]:
    """Числовые ячейки первого листа книги по адресу, например `C7`."""
    with ZipFile(BytesIO(content)) as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return {
        cell.attrib["r"]: float(cell.find("m:v", NS).text)
        for cell in sheet.findall(".//m:c", NS)
        if "t" not in cell.attrib and cell.find("m:v", NS) is not None
    }


def _column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _layout(project: dict) -> tuple[list[str], list[dict]]:
    configuration = project["configuration"]
    codes = [item["code"] for item in configuration["questions"] if item["included_in_report"]]
    return codes, configuration["banners"][0]["blocks"]


def test_every_number_of_the_table_is_the_number_in_the_workbook(tmp_path: Path) -> None:
    source = tmp_path / "significance.sav"
    project = _significance_project(source)
    codes, blocks = _layout(project)

    table = build_live_table(source, project, questions=codes, blocks=blocks)
    workbook = _numeric_cells(build_topline_xlsx(source, project))

    first_body_row = min(row["sheet_row"] for q in table["questions"] for row in q["rows"])
    shown = {}
    for question in table["questions"]:
        for row in question["rows"]:
            for index, cell in enumerate(row["cells"], start=2):
                if cell["value"] is not None:
                    shown[f"{_column_letter(index)}{row['sheet_row']}"] = cell["value"]
    in_body = {
        ref: value
        for ref, value in workbook.items()
        if int("".join(filter(str.isdigit, ref))) >= first_body_row
    }
    assert shown, "таблица пустая"
    assert shown.keys() == in_body.keys()
    for ref, value in shown.items():
        assert value == pytest.approx(in_body[ref]), ref
    # Базы шапки — тоже из книги.
    assert [column["base"] for column in table["columns"]] == [
        int(workbook[f"{_column_letter(index)}5"])
        for index in range(2, len(table["columns"]) + 2)
    ]


def test_cells_carry_significance_letters_and_the_test_protocol(tmp_path: Path) -> None:
    source = tmp_path / "significance.sav"
    project = _significance_project(source)
    codes, blocks = _layout(project)

    table = build_live_table(source, project, questions=["OUTCOME"], blocks=blocks)

    assert [column["letter"] for column in table["columns"]] == ["A", "B", "C"]
    assert [column["base"] for column in table["columns"]] == [100, 60, 40]
    assert table["blocks"] == [{"label": "Группа", "first": 1, "last": 2}]
    yes = next(row for row in table["questions"][0]["rows"] if row["label"] == "Да")
    first = yes["cells"][1]
    assert first["value"] == pytest.approx(70.0)
    assert first["direction"] == "higher"
    assert first["higher_than"] == ["C"]
    assert yes["cells"][2]["lower_than"] == ["B"]
    # Протокол — тот же текст, что в statistics.txt.
    assert "Метод: z-test" in first["protocol"]
    assert "p-value=" in first["protocol"]


def test_layout_is_applied_to_a_copy_and_the_project_stays_as_saved(tmp_path: Path) -> None:
    source = tmp_path / "significance.sav"
    project = _significance_project(source)
    saved = copy.deepcopy(project)

    table = build_live_table(source, project, questions=["OUTCOME"])

    assert [column["label"] for column in table["columns"]] == ["Total"]
    assert [question["code"] for question in table["questions"]] == ["OUTCOME"]
    assert project == saved


def test_table_refuses_unknown_and_unsupported_rows(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    from sav_analytics.core.sav_reader import inspect_sav

    inspection = inspect_sav(source).to_dict()
    project = {
        "name": "Отказы",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "banners": [],
            "filters": [],
            "report_filter_id": None,
        },
    }
    open_text = next(
        item["code"] for item in inspection["questions"] if item["question_type"] == "open_text"
    )

    with pytest.raises(ReportError, match="не найдены: NOPE"):
        build_live_table(source, project, questions=["NOPE"])
    with pytest.raises(ReportError, match=open_text):
        build_live_table(source, project, questions=[open_text])
    with pytest.raises(ReportError, match="Баннер не найден"):
        build_live_table(
            source, project, questions=["Q1"], banner_id="00000000-0000-0000-0000-000000000000"
        )


def test_table_preview_endpoint(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects",
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()["id"]
            url = f"/api/projects/{project_id}/tables/preview"

            response = client.post(
                url,
                json={
                    "questions": ["Q1"],
                    "blocks": [{"label": "Пол", "sources": [{"kind": "question", "ref": "Q1"}]}],
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert [column["base"] for column in body["columns"]] == [4, 2, 2]
            assert body["questions"][0]["code"] == "Q1"

            refused = client.post(url, json={"questions": ["NOPE"]})
            assert refused.status_code == 422
            both = client.post(
                url,
                json={
                    "questions": ["Q1"],
                    "banner_id": "00000000-0000-0000-0000-000000000000",
                    "blocks": [{"sources": [{"kind": "question", "ref": "Q1"}]}],
                },
            )
            assert both.status_code == 422
    finally:
        app.dependency_overrides.clear()
