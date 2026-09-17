"""Формулы (PQ.9): разбор, правила пропусков и путь до книги."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.formulas import FormulaError, evaluate_formula, formula_variables
from sav_analytics.core.report import build_topline_xlsx
from sav_analytics.repository import ProjectRepository
from tests.test_report import _cell_values, _row_labels
from tests.test_sav_reader import write_fixture


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "A": [1.0, 2.0, np.nan, 4.0],
            "B": [3.0, np.nan, np.nan, 0.0],
            "Q1.2": [1.0, 0.0, 1.0, 1.0],
        }
    )


def _values(expression: str) -> list[float | None]:
    series = evaluate_formula(expression, _frame())
    return [None if pd.isna(value) else float(value) for value in series]


def test_arithmetic_propagates_missing_and_division_by_zero() -> None:
    assert _values("A + B * 2") == [7.0, None, None, 4.0]
    assert _values("A / B") == [pytest.approx(1 / 3), None, None, None]
    assert _values("-A + 1") == [0.0, -1.0, None, -3.0]


def test_aggregates_skip_missing_like_spss() -> None:
    assert _values("SUM(A, B)") == [4.0, 2.0, None, 4.0]
    assert _values("MEAN(A, B)") == [2.0, 2.0, None, 2.0]
    assert _values("MIN(A, B)") == [1.0, 2.0, None, 0.0]
    assert _values("max(A, B)") == [3.0, 2.0, None, 4.0]
    assert _values("COUNT(1, A, [Q1.2])") == [2.0, 0.0, 1.0, 1.0]


def test_conditions_are_three_valued() -> None:
    assert _values("IF(A >= 2, 10, 20)") == [20.0, 10.0, None, 10.0]
    # ложь AND пропуск — ложь, истина AND пропуск — пропуск; истина OR пропуск — истина.
    assert _values("A = 2 AND B = 1") == [0.0, None, None, 0.0]
    assert _values("A = 2 or B = 1") == [0.0, 1.0, None, 0.0]
    assert _values("A <> 1") == [0.0, 1.0, None, 1.0]
    assert _values("NOT A = 1") == [0.0, 1.0, None, 1.0]
    assert _values("ROUND(A / 3, 1)") == [0.3, 0.7, None, 1.3]


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "A.real",
        "'текст'",
        "LOG(A)",
        "A ** 2",
        "1 < A < 3",
        "IF(A, 1)",
        "A +",
        "lambda: 1",
    ],
)
def test_unsafe_or_wrong_expressions_are_refused(expression: str) -> None:
    with pytest.raises(FormulaError):
        evaluate_formula(expression, _frame())


def test_variables_are_listed_without_function_names() -> None:
    assert formula_variables("MEAN(A, [Q1.2]) + IF(B > 0, A, 0)") == ["A", "Q1.2", "B"]


def test_formula_becomes_a_numeric_question_with_preview(tmp_path: Path) -> None:
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
            base = f"/api/projects/{project['id']}"
            definition = {"name": "SCORE", "label": "Оценка ×10", "expression": "Q2 * 10"}

            preview = client.post(f"{base}/formulas/preview", json=definition)
            assert preview.status_code == 200
            body = preview.json()
            assert (body["valid"], body["missing"]) == (3, 1)
            assert body["mean"] == pytest.approx((100 + 90 + 70) / 3)
            assert body["missing_by_variable"] == [
                {"variable": "Q2", "label": "Оцените сервис от 0 до 10", "count": 1}
            ]

            wrong = client.post(
                f"{base}/formulas/preview", json={**definition, "expression": "NOPE + 1"}
            )
            assert wrong.status_code == 422
            assert "NOPE" in wrong.json()["detail"]
            text = client.post(
                f"{base}/formulas/preview", json={**definition, "expression": "Q4_open + 1"}
            )
            assert text.status_code == 422

            created = client.post(f"{base}/formulas", json=definition)
            assert created.status_code == 201
            configuration = created.json()["configuration"]
            formula = configuration["formulas"][0]
            question = next(item for item in configuration["questions"] if item["code"] == "SCORE")
            assert question["question_type"] == "numeric"
            assert question["included_in_report"] is True
            assert (question["valid_count"], question["missing_count"]) == (3, 1)

            duplicate = client.post(f"{base}/formulas", json=definition)
            assert duplicate.status_code == 422

            question_preview = client.get(f"{base}/questions/SCORE/preview")
            assert question_preview.status_code == 200

            # Перераспознавание структуры формулу не теряет.
            refreshed = client.post(f"{base}/structure/refresh").json()
            assert any(item["code"] == "SCORE" for item in refreshed["configuration"]["questions"])

            updated = client.put(
                f"{base}/formulas/{formula['id']}",
                json={**definition, "label": "Оценка", "expression": "SUM(Q2, 0) * 10"},
            )
            assert updated.status_code == 200
            question = next(
                item for item in updated.json()["configuration"]["questions"]
                if item["code"] == "SCORE"
            )
            assert (question["label"], question["missing_count"]) == ("Оценка", 0)
            renamed = client.put(
                f"{base}/formulas/{formula['id']}", json={**definition, "name": "OTHER"}
            )
            assert renamed.status_code == 422

            stored = repository.get(project["id"])
            content = build_topline_xlsx(repository.source_path(project["id"]), stored)
            assert any(
                label.startswith("SCORE") and "Оценка" in label for label in _row_labels(content)
            )
            # SUM(Q2, 0) у пропуска даёт 0: (100 + 90 + 70 + 0) / 4.
            assert pytest.approx(65.0) in _cell_values(content, "Среднее", "B")

            deleted = client.delete(f"{base}/formulas/{formula['id']}")
            assert deleted.status_code == 200
            configuration = deleted.json()["configuration"]
            assert configuration["formulas"] == []
            assert all(item["code"] != "SCORE" for item in configuration["questions"])
            assert all(
                item["name"] != "SCORE" for item in deleted.json()["inspection"]["variables"]
            )
    finally:
        app.dependency_overrides.clear()


def test_formula_can_be_built_on_another_formula(tmp_path: Path) -> None:
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
            base = f"/api/projects/{project['id']}"
            first = client.post(
                f"{base}/formulas", json={"name": "S10", "label": "×10", "expression": "Q2 * 10"}
            ).json()["configuration"]["formulas"][0]
            second = client.post(
                f"{base}/formulas",
                json={"name": "S100", "label": "×100", "expression": "S10 * 10"},
            )
            assert second.status_code == 201
            question = next(
                item for item in second.json()["configuration"]["questions"]
                if item["code"] == "S100"
            )
            assert (question["valid_count"], question["missing_count"]) == (3, 1)

            preview = client.post(
                f"{base}/formulas/preview",
                json={"name": "S1000", "label": "×1000", "expression": "S100 * 10"},
            ).json()
            assert preview["max"] == 10000
            assert preview["missing_by_variable"][0]["variable"] == "S100"

            # Цикл: S10 не может ссылаться на S100, который построен на S10.
            loop = client.put(
                f"{base}/formulas/{first['id']}",
                json={"name": "S10", "label": "×10", "expression": "S100 + 1"},
            )
            assert loop.status_code == 422
            assert "по кругу" in loop.json()["detail"]

            blocked = client.delete(f"{base}/formulas/{first['id']}")
            assert blocked.status_code == 422
            assert "S100" in blocked.json()["detail"]

            stored = repository.get(project["id"])
            content = build_topline_xlsx(repository.source_path(project["id"]), stored)
            assert pytest.approx((1000 + 900 + 700) / 3) in _cell_values(content, "Среднее", "B")
    finally:
        app.dependency_overrides.clear()

