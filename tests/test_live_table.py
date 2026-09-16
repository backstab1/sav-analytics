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
from sav_analytics.core.reporting.live import (
    _needed_columns,
    build_live_table,
    export_live_table,
)
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


def test_cells_carry_the_index_to_total(tmp_path: Path) -> None:
    source = tmp_path / "significance.sav"
    project = _significance_project(source)
    _, blocks = _layout(project)

    table = build_live_table(source, project, questions=["OUTCOME"], blocks=blocks)

    yes = next(row for row in table["questions"][0]["rows"] if row["label"] == "Да")
    # 70% в первой группе против 62% по всей выборке.
    assert yes["cells"][0]["index"] == pytest.approx(100.0)
    assert yes["cells"][1]["index"] == pytest.approx(70 / 62 * 100)
    # База — не доля, индекса у неё нет.
    base_rows = [row for row in table["questions"][0]["rows"] if row["kind"] == "base"]
    assert all("index" not in cell for row in base_rows for cell in row["cells"])


def test_only_the_columns_of_the_layout_are_read(tmp_path: Path) -> None:
    """Пересчёт таблицы не читает весь массив (роадмап, PQ.3)."""
    source = tmp_path / "significance.sav"
    project = _significance_project(source)
    configuration = project["configuration"]
    configuration["banners"][0]["id"] = "banner"
    configuration["report_banner_id"] = "banner"
    configuration["filters"] = [
        {
            "id": "men",
            "name": "Первая группа",
            "rule": {
                "kind": "group",
                "operator": "and",
                "items": [
                    {
                        "kind": "condition",
                        "source": {"kind": "question", "ref": "GROUP"},
                        "operator": "in",
                        "values": [1],
                    }
                ],
            },
        }
    ]
    configuration["report_filter_id"] = "men"
    for question in configuration["questions"]:
        question["included_in_report"] = question["code"] == "OUTCOME"

    assert _needed_columns(project) == ["OUTCOME", "GROUP"]

    # Рассчитанный вес читает массив сам, поэтому столбцы не ограничиваются.
    configuration["report_settings"]["calculated_weight_id"] = "whatever"
    assert _needed_columns(project) is None


def test_export_gives_the_workbook_of_the_same_layout(tmp_path: Path) -> None:
    """Выгрузка таблицы — та же книга, что соответствующая часть отчёта."""
    source = tmp_path / "significance.sav"
    project = _significance_project(source)
    _, blocks = _layout(project)

    workbook, audit = export_live_table(
        source, project, questions=["OUTCOME"], blocks=blocks
    )

    assert workbook.startswith(b"PK")
    cells = _numeric_cells(workbook)
    table = build_live_table(source, project, questions=["OUTCOME"], blocks=blocks)
    shown = {
        f"{_column_letter(index)}{row['sheet_row']}": cell["value"]
        for row in table["questions"][0]["rows"]
        for index, cell in enumerate(row["cells"], start=2)
        if cell["value"] is not None
    }
    for ref, value in shown.items():
        assert cells[ref] == pytest.approx(value), ref
    assert "СТАТИСТИЧЕСКИЙ АУДИТ ТОПЛАЙНА" in audit


def test_export_endpoint_returns_a_named_workbook(tmp_path: Path) -> None:
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

            response = client.post(
                f"/api/projects/{project_id}/tables/export",
                json={"questions": ["Q1"]},
            )

            assert response.status_code == 200
            assert response.content.startswith(b"PK")
            assert "filename*=UTF-8''" in response.headers["content-disposition"]
            assert response.headers["content-disposition"].endswith("%D1%86%D0%B0.xlsx")
    finally:
        app.dependency_overrides.clear()


def test_export_of_the_whole_report_takes_every_included_question(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()
            url = f"/api/projects/{project['id']}/tables/export"
            included = [
                item for item in project["configuration"]["questions"] if item["included_in_report"]
            ]
            other = next(item for item in included if item["code"] != "Q1")

            table = client.post(url, json={"questions": ["Q1"], "scope": "table"})
            report = client.post(url, json={"questions": ["Q1"], "scope": "report"})

            assert table.status_code == report.status_code == 200
            assert report.headers["content-disposition"].endswith(".xlsx")

            def strings(content: bytes) -> str:
                with ZipFile(BytesIO(content)) as archive:
                    return archive.read("xl/sharedStrings.xml").decode("utf-8")

            assert other["label"] not in strings(table.content)
            assert other["label"] in strings(report.content)
    finally:
        app.dependency_overrides.clear()


def test_count_rows_reach_the_table_as_bases(tmp_path: Path) -> None:
    source = tmp_path / "significance.sav"
    project = _significance_project(source, show_counts=True)
    _, blocks = _layout(project)

    table = build_live_table(source, project, questions=["OUTCOME"], blocks=blocks)

    rows = {row["label"]: row for row in table["questions"][0]["rows"]}
    counts = rows["Да, N"]
    assert counts["kind"] == "base"
    assert [cell["value"] for cell in counts["cells"]] == [62, 42, 20]
    assert all("index" not in cell for cell in counts["cells"])


def test_row_percents_reach_the_table_without_index(tmp_path: Path) -> None:
    source = tmp_path / "significance.sav"
    project = _significance_project(source, row_percents=True)
    _, blocks = _layout(project)

    table = build_live_table(source, project, questions=["OUTCOME"], blocks=blocks)

    rows = {row["label"]: row for row in table["questions"][0]["rows"]}
    shares = rows["Да, % по строке"]
    assert shares["kind"] == "value"
    assert shares["cells"][1]["value"] == pytest.approx(42 / 62 * 100)
    assert all("index" not in cell for cell in shares["cells"])


def test_table_separates_letters_of_the_second_confidence_level(tmp_path: Path) -> None:
    from tests.test_report import _secondary_project

    source = tmp_path / "secondary.sav"
    project = _secondary_project(source, secondary_confidence_level=0.9)
    blocks = project["configuration"]["banners"][0]["blocks"]

    table = build_live_table(source, project, questions=["OUTCOME"], blocks=blocks)

    yes = next(row for row in table["questions"][0]["rows"] if row["label"] == "Да")
    assert yes["cells"][1]["higher_than"] == []
    assert yes["cells"][1]["higher_than_secondary"] == ["c"]
    assert table["settings"]["secondary_confidence_level"] == 0.9

