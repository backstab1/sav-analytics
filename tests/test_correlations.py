"""Лист Correlations: связи числовых вопросов книги (P1.2, PQ.10)."""

from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pyreadstat
import pytest
from scipy import stats

from sav_analytics.core.report import build_topline_xlsx
from sav_analytics.core.sav_reader import inspect_sav


def _project(source: Path, **settings: object) -> tuple[dict, pd.DataFrame]:
    generator = np.random.default_rng(3)
    size = 120
    score = generator.normal(6, 2, size)
    frame = pd.DataFrame(
        {
            "SCORE": np.round(score, 2),
            "INCOME": np.round(score * 3 + generator.normal(0, 4, size), 2),
            "AGE": np.round(generator.normal(40, 10, size), 2),
            "SEX": np.tile([1, 2], size // 2),
        }
    )
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"SCORE": "Оценка", "INCOME": "Доход", "AGE": "Возраст", "SEX": "Пол"},
        variable_value_labels={"SEX": {1: "М", 2: "Ж"}},
        variable_measure={"SCORE": "scale", "INCOME": "scale", "AGE": "scale", "SEX": "nominal"},
    )
    inspection = inspect_sav(source).to_dict()
    for question in inspection["questions"]:
        if question["code"] != "SEX":
            question["question_type"] = "numeric"
    return {
        "name": "Корреляции",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "filters": [],
            "banners": [],
            "report_settings": {"minimum_base": 30, **settings},
        },
    }, frame


def _sheet_values(content: bytes, sheet_name: str) -> tuple[str, dict[str, float]]:
    """Числа листа по адресам ячеек. Файл листа ищется по связям книги."""
    namespace = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "p": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with ZipFile(BytesIO(content)) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relations = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        sheet = next(
            item
            for item in workbook.iter(f"{{{namespace['m']}}}sheet")
            if item.get("name") == sheet_name
        )
        target = next(
            item.get("Target")
            for item in relations.iter(f"{{{namespace['p']}}}Relationship")
            if item.get("Id") == sheet.get(f"{{{namespace['r']}}}id")
        )
        root = ElementTree.fromstring(archive.read(f"xl/{target.lstrip('/')}"))
        strings = archive.read("xl/sharedStrings.xml").decode("utf-8")
    numbers = {}
    for cell in root.iter(f"{{{namespace['m']}}}c"):
        value = cell.find(f"{{{namespace['m']}}}v")
        if value is not None and cell.get("t") != "s":
            numbers[cell.get("r")] = float(value.text)
    return strings, numbers


def test_correlations_sheet_matches_scipy(tmp_path: Path) -> None:
    source = tmp_path / "survey.sav"
    project, frame = _project(source, correlations=True)

    content = build_topline_xlsx(source, project)
    strings, numbers = _sheet_values(content, "Correlations")

    assert "Пирсон" in strings
    # Порядок вопросов: SCORE, INCOME, AGE. Первая строка матрицы — B5, C5, D5.
    reference = stats.pearsonr(frame["SCORE"], frame["INCOME"]).statistic
    assert numbers["C5"] == pytest.approx(reference, abs=5e-3)
    assert numbers["B6"] == pytest.approx(reference, abs=5e-3)
    assert numbers["D5"] == pytest.approx(
        stats.pearsonr(frame["SCORE"], frame["AGE"]).statistic, abs=5e-3
    )


def test_correlations_sheet_is_absent_by_default(tmp_path: Path) -> None:
    source = tmp_path / "survey.sav"
    project, _ = _project(source)

    content = build_topline_xlsx(source, project)

    with ZipFile(BytesIO(content)) as archive:
        assert "Correlations" not in archive.read("xl/workbook.xml").decode("utf-8")
