"""Новая волна в существующий проект (PQ.8)."""

from pathlib import Path

import pandas as pd
import pyreadstat
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.repository import ProjectRepository


def _write(path: Path, *, rows: int, labels: dict[int, str], extra: bool = False) -> None:
    frame = pd.DataFrame(
        {
            "SEX": [1 if index % 2 else 2 for index in range(rows)],
            "SCORE": [index % 11 for index in range(rows)],
        }
    )
    columns = {"SEX": "Пол", "SCORE": "Оценка"}
    if extra:
        frame["CITY"] = [1 for _ in range(rows)]
        columns["CITY"] = "Город"
    pyreadstat.write_sav(
        frame,
        path,
        column_labels=columns,
        variable_value_labels={"SEX": labels},
        variable_measure={"SEX": "nominal", "SCORE": "scale"},
    )


def _upload(client: TestClient, path: Path, url: str, method: str = "post"):
    with path.open("rb") as stream:
        return getattr(client, method)(
            url, files={"file": (path.name, stream, "application/octet-stream")}
        )


def test_new_wave_keeps_configuration_and_reports_structure_changes(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    first = tmp_path / "wave1.sav"
    second = tmp_path / "wave2.sav"
    _write(first, rows=40, labels={1: "Мужчина", 2: "Женщина"})
    _write(second, rows=60, labels={1: "Мужчины", 2: "Женщины"}, extra=True)
    try:
        with TestClient(app) as client:
            project = _upload(client, first, "/api/projects").json()
            base = f"/api/projects/{project['id']}"
            assert client.patch(
                f"{base}/questions/SCORE", json={"question_type": "numeric", "label": "Оценка 0–10"}
            ).status_code == 200
            assert client.post(
                f"{base}/formulas",
                json={"name": "SCORE2", "label": "Удвоенная", "expression": "SCORE * 2"},
            ).status_code == 201

            diff = _upload(client, second, f"{base}/source/diff").json()
            assert diff["can_replace"] is True
            assert [item["name"] for item in diff["added"]] == ["CITY"]
            assert diff["removed"] == []
            assert diff["rows_before"], diff["rows_after"] == (40, 60)
            sex = next(item for item in diff["changed"] if item["name"] == "SEX")
            assert "подписи значений" in " ".join(sex["changes"])

            replaced = _upload(client, second, f"{base}/source", method="put")
            assert replaced.status_code == 200, replaced.text
            configuration = replaced.json()["project"]["configuration"]
            score = next(item for item in configuration["questions"] if item["code"] == "SCORE")
            assert (score["question_type"], score["label"]) == ("numeric", "Оценка 0–10")
            formula = next(item for item in configuration["questions"] if item["code"] == "SCORE2")
            assert formula["valid_count"] == 60
            variables = replaced.json()["project"]["inspection"]["variables"]
            assert any(item["name"] == "CITY" for item in variables)

            table = client.post(
                f"{base}/tables/preview", json={"questions": ["SCORE"]}
            ).json()
            assert table["columns"][0]["base"] == 60
    finally:
        app.dependency_overrides.clear()


def test_wave_that_loses_a_used_variable_is_refused(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    first = tmp_path / "wave1.sav"
    broken = tmp_path / "wave-short.sav"
    _write(first, rows=40, labels={1: "Мужчина", 2: "Женщина"})
    pyreadstat.write_sav(
        pd.DataFrame({"SEX": [1, 2, 1, 2]}),
        broken,
        column_labels={"SEX": "Пол"},
        variable_value_labels={"SEX": {1: "Мужчина", 2: "Женщина"}},
    )
    try:
        with TestClient(app) as client:
            project = _upload(client, first, "/api/projects").json()
            base = f"/api/projects/{project['id']}"

            diff = _upload(client, broken, f"{base}/source/diff").json()
            assert diff["can_replace"] is False
            assert any("SCORE" in problem for problem in diff["blocking"])

            refused = _upload(client, broken, f"{base}/source", method="put")
            assert refused.status_code == 422
            assert "SCORE" in refused.json()["detail"]

            # Проект остался на прежних данных.
            current = client.get(base).json()
            assert current["inspection"]["row_count"] == 40
    finally:
        app.dependency_overrides.clear()
