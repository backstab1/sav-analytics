"""Шаблон целей веса: выгрузка по редактору и загрузка заполненного (§10)."""

from io import BytesIO
from pathlib import Path

import pytest
import xlsxwriter
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.tabular_import import _read_xlsx
from sav_analytics.core.weight_targets import (
    WeightTargetError,
    build_target_template,
    read_target_file,
)
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture

DEFINITION = {
    "method": "raking",
    "dimensions": [
        {
            "variable": "Q1",
            "label": "Пол",
            "targets": [
                {"label": "Мужчина", "values": [1], "percent": 50},
                {"label": "Женщина", "values": [2], "percent": 50},
            ],
        }
    ],
}


def _workbook(rows: list[list[object]]) -> bytes:
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    sheet = workbook.add_worksheet()
    for index, row in enumerate(rows):
        sheet.write_row(index, 0, row)
    workbook.close()
    return output.getvalue()


def test_template_lists_every_category_with_its_key(tmp_path: Path) -> None:
    path = tmp_path / "template.xlsx"
    path.write_bytes(build_target_template(DEFINITION))

    table = _read_xlsx(path)

    assert list(table.columns) == ["Ключ", "Переменная", "Категория", "Код", "Цель, %"]
    assert table["Ключ"].tolist() == ["Q1", "Q1"]
    assert table["Категория"].tolist() == ["Мужчина", "Женщина"]


def test_cell_template_names_each_combination(tmp_path: Path) -> None:
    path = tmp_path / "cells.xlsx"
    path.write_bytes(
        build_target_template(
            {
                "method": "cells",
                "dimensions": DEFINITION["dimensions"],
                "cells": [
                    {"categories": ["Мужчина", "18–34"], "percent": 30},
                    {"categories": ["Женщина", "18–34"], "percent": 70},
                ],
            }
        )
    )

    table = _read_xlsx(path)

    assert table["Ключ"].tolist() == ["ячейка", "ячейка"]
    assert table["Категория"].tolist() == ["Мужчина × 18–34", "Женщина × 18–34"]


def test_filled_template_is_read_back_with_comma_decimals() -> None:
    content = _workbook(
        [
            ["Ключ", "Переменная", "Категория", "Код", "Цель, %"],
            ["Q1", "Пол", "Мужчина", "1", "48,5"],
            ["Q1", "Пол", "Женщина", "2", 51.5],
            ["", "", "", "", ""],
        ]
    )

    rows = read_target_file("targets.xlsx", BytesIO(content))

    assert rows == [
        {"key": "Q1", "category": "Мужчина", "code": "1", "percent": 48.5},
        {"key": "Q1", "category": "Женщина", "code": "2", "percent": 51.5},
    ]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([["Переменная", "Цель"], ["Q1", 50]], "Ключ"),
        ([["Ключ", "Категория", "Цель, %"], ["Q1", "Мужчина", "много"]], "не число"),
        ([["Ключ", "Категория", "Цель, %"], ["Q1", "Мужчина", 150]], "вне 0–100"),
    ],
)
def test_broken_target_file_is_explained(rows: list[list[object]], message: str) -> None:
    with pytest.raises(WeightTargetError, match=message):
        read_target_file("targets.xlsx", BytesIO(_workbook(rows)))


def test_target_file_must_be_a_table() -> None:
    with pytest.raises(WeightTargetError, match="XLSX или CSV"):
        read_target_file("targets.pdf", BytesIO(b"%PDF"))


def test_template_round_trip_through_the_api(tmp_path: Path) -> None:
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
            template = client.post(
                f"/api/projects/{project_id}/weights/targets-template", json=DEFINITION
            )
            assert template.status_code == 200
            assert template.content.startswith(b"PK")

            imported = client.post(
                f"/api/projects/{project_id}/weights/targets-import",
                files={"file": ("targets.xlsx", template.content, "application/octet-stream")},
            )
            assert imported.status_code == 200
            assert [row["percent"] for row in imported.json()["rows"]] == [50, 50]

            refused = client.post(
                f"/api/projects/{project_id}/weights/targets-import",
                files={"file": ("notes.txt", b"hello", "text/plain")},
            )
            assert refused.status_code == 422
    finally:
        app.dependency_overrides.clear()
