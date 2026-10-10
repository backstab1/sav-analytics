"""Презентация PPTX (PQ.13): числа и значимость слайда — запись листа книги."""

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from sav_analytics.core.reporting.builder import build_topline_artifacts
from sav_analytics.core.reporting.data import prepare_report_data
from sav_analytics.core.reporting.live import build_live_table
from sav_analytics.core.reporting.presentation import (
    MIN_COLUMN,
    NARROW_BAR,
    NARROW_LABEL,
    WIDTH,
    _column_pages,
    _wrap_lines,
    build_pptx,
    presentation_from_data,
)
from tests.test_api import _prepare_report
from tests.test_report import _significance_project
from tests.test_report_books import _settings, project  # noqa: F401 - фикстура

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def test_slide_numbers_and_significance_are_the_workbook_sheet(tmp_path: Path) -> None:
    source = tmp_path / "significance.sav"
    project_data = _significance_project(source)
    blocks = project_data["configuration"]["banners"][0]["blocks"]

    presentation = presentation_from_data(prepare_report_data(source, project_data), project_data)
    live = build_live_table(source, project_data, questions=["OUTCOME"], blocks=blocks)

    slide = next(item for item in presentation.slides if item.code == "OUTCOME")
    shown = {row["label"]: row["cells"] for row in live["questions"][0]["rows"]}
    assert slide.measure == "% от всех опрошенных"
    assert [column["letter"] for column in slide.columns] == ["A", "B", "C"]
    for row in slide.rows:
        cells = shown[row.label]
        assert row.value == pytest.approx(cells[0]["value"])
        for cell, expected in zip(row.cells, cells, strict=True):
            assert cell.direction == expected["direction"]
            assert cell.letters == "".join(expected["higher_than"])
    assert any(cell.direction for row in slide.rows for cell in row.cells[1:])


def test_matrix_shows_means_and_numeric_its_mean(tmp_path: Path) -> None:
    from tests.test_report import _output_frame, _output_project

    source = tmp_path / "output.sav"
    project_data = _output_project(source, _output_frame())
    presentation = presentation_from_data(prepare_report_data(source, project_data), project_data)

    by_type = {
        item["code"]: item["question_type"]
        for item in project_data["configuration"]["questions"]
    }
    for slide in presentation.slides:
        if by_type[slide.code] in {"matrix", "numeric"}:
            assert slide.measure == "Среднее"
        assert slide.rows, slide.code


def test_package_opens_as_a_presentation(tmp_path: Path) -> None:
    source = tmp_path / "significance.sav"
    project_data = _significance_project(source, presentation=True)

    artifacts = build_topline_artifacts(source, project_data)

    assert artifacts.pptx is not None
    with zipfile.ZipFile(io.BytesIO(artifacts.pptx)) as archive:
        names = archive.namelist()
        types = archive.read("[Content_Types].xml").decode("utf-8")
        slides = sorted(name for name in names if name.startswith("ppt/slides/slide"))
        for name in names:
            if name.endswith((".xml", ".rels")):
                ElementTree.fromstring(archive.read(name))
        texts = [
            "".join(node.text or "" for node in ElementTree.fromstring(archive.read(name)).iter(
                f"{{{NS['a']}}}t"
            ))
            for name in slides
        ]
    for name in slides:
        assert f'PartName="/{name}"' in types
    # Титул, слайды вопросов и параметры отчёта.
    assert len(slides) >= 3
    assert any("OUTCOME" in text for text in texts)
    assert any("Параметры отчёта" in text for text in texts)


def test_presentation_is_built_only_when_the_book_asks(tmp_path: Path) -> None:
    source = tmp_path / "significance.sav"
    assert build_topline_artifacts(source, _significance_project(source)).pptx is None


def _columns(blocks: list[int]) -> list[dict]:
    columns = [{"letter": "A", "block": ""}]
    for number, size in enumerate(blocks):
        columns.extend({"block": f"Блок {number}"} for _ in range(size))
    return columns


def test_wide_banner_continues_on_the_next_slide_by_whole_blocks() -> None:
    per_page = int((WIDTH - NARROW_LABEL - NARROW_BAR) // MIN_COLUMN)
    assert _column_pages(_columns([2, 5, 4])) == [list(range(1, 12))]

    pages = _column_pages(_columns([2, 5, 4, 4]))

    assert [len(page) for page in pages] == [7, 8]
    assert [index for page in pages for index in page] == list(range(1, 16))
    # Блок, который сам шире слайда, режется; остальные — нет.
    huge = _column_pages(_columns([per_page + 3]))
    assert sum(len(page) for page in huge) == per_page + 3
    assert all(len(page) <= per_page for page in huge)


def test_long_words_take_their_lines() -> None:
    assert _wrap_lines("Мужчина", 1.0, 8, bold=True) == 1
    assert _wrap_lines("Другой город-миллионник", 0.62, 8, bold=True) == 3


def test_build_writes_the_presentation_beside_the_workbook(project) -> None:  # noqa: F811
    client, project_id, _ = project
    plain = _prepare_report(client, project_id)
    assert "presentation" not in plain["downloads"]

    _settings(client, project_id, presentation=True)
    built = _prepare_report(client, project_id)

    response = client.get(built["downloads"]["presentation"])
    assert response.status_code == 200
    assert ".pptx" in response.headers["content-disposition"]
    assert response.content.startswith(b"PK")
    current = client.get(f"/api/projects/{project_id}/reports/presentation.pptx")
    assert current.content == response.content
    history = client.get(f"/api/projects/{project_id}/reports/history").json()
    runs = {run["artifact_id"]: run for run in history["runs"]}
    assert "presentation" in runs[built["artifact_id"]]["downloads"]
    assert "presentation" not in runs[plain["artifact_id"]]["downloads"]
    missing = client.get(plain["downloads"]["topline"].replace("topline.xlsx", "presentation.pptx"))
    assert missing.status_code == 404


def test_presentation_slide_is_rendered_with_the_build_setting() -> None:
    from sav_analytics.core.reporting.presentation import Presentation

    content = build_pptx(Presentation(title="Пусто", subtitle=["—"]))
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert "ppt/slides/slide1.xml" in archive.namelist()
