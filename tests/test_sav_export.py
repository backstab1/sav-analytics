"""Выгрузка SAV с производными переменными (PQ.9)."""

from pathlib import Path

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.repository import ProjectRepository


def _write_source(path: Path) -> None:
    frame = pd.DataFrame(
        {
            "SEX": [1, 2, 1, 2, 1, 2],
            "AGE": [18.0, 25.0, 40.0, 61.0, 33.0, 99.0],
            "SAT": [5.0, 4.0, 3.0, 5.0, 1.0, 2.0],
        }
    )
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={"SEX": "Пол", "AGE": "Возраст", "SAT": "Удовлетворённость"},
        variable_value_labels={"SEX": {1: "Мужчина", 2: "Женщина"}},
        missing_ranges={"AGE": [99]},
        variable_measure={"SEX": "nominal", "AGE": "scale", "SAT": "scale"},
    )


def test_export_adds_formulas_recodings_and_keeps_source_metadata(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "survey.sav"
    _write_source(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                files={"file": ("survey.sav", stream, "application/octet-stream")},
            ).json()
            base = f"/api/projects/{project['id']}"
            assert client.post(
                f"{base}/formulas",
                json={"name": "SAT100", "label": "Удовлетворённость 0–100",
                      "expression": "(SAT - 1) * 25"},
            ).status_code == 201
            assert client.post(
                f"{base}/recodings",
                json={
                    "mode": "ranges",
                    "code": "AGEGR",
                    "name": "Возрастные группы",
                    "source_variable": "AGE",
                    "categories": [
                        {"label": "До 30", "lower": None, "upper": 29},
                        {"label": "30 и старше", "lower": 30, "upper": None},
                    ],
                },
            ).status_code == 201
            assert client.patch(
                f"{base}/questions/SEX", json={"label": "Пол респондента"}
            ).status_code == 200

            response = client.get(f"{base}/export.sav")
            assert response.status_code == 200
            exported = tmp_path / "exported.sav"
            exported.write_bytes(response.content)
    finally:
        app.dependency_overrides.clear()

    frame, meta = pyreadstat.read_sav(exported, user_missing=True)
    assert list(frame.columns) == ["SEX", "AGE", "SAT", "SAT100", "AGEGR"]
    assert frame["SAT100"].tolist() == [100.0, 75.0, 50.0, 100.0, 0.0, 25.0]
    # 99 — объявленный пропуск возраста: в группу не попадает, а в AGE остаётся.
    assert frame["AGEGR"].tolist()[:5] == [1.0, 1.0, 2.0, 2.0, 2.0]
    assert pd.isna(frame["AGEGR"].iloc[5])
    assert frame["AGE"].iloc[5] == 99
    assert meta.variable_value_labels["AGEGR"] == {1.0: "До 30", 2.0: "30 и старше"}
    assert meta.column_names_to_labels["SEX"] == "Пол респондента"
    assert meta.column_names_to_labels["SAT100"] == "Удовлетворённость 0–100"
    assert meta.variable_value_labels["SEX"] == {1.0: "Мужчина", 2.0: "Женщина"}
    assert meta.missing_ranges["AGE"] == [{"lo": 99.0, "hi": 99.0}]
    assert meta.variable_measure["SAT100"] == "scale"


def test_export_of_missing_project_is_404(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            response = client.get("/api/projects/00000000-0000-0000-0000-000000000000/export.sav")
            assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_written_sav_is_checked_against_the_writer_bug(tmp_path: Path) -> None:
    """readstat называет части длинной строки Q17.10 именем Q17.11 — файл портится."""
    from sav_analytics.core.sav_writing import SavWriteMismatchError, verify_written_sav

    target = tmp_path / "broken.sav"
    names = ["Q17.10", "Q17.11", "Q17.12"]
    pyreadstat.write_sav(pd.DataFrame({name: ["я" * 200] for name in names}), target)

    with pytest.raises(SavWriteMismatchError):
        verify_written_sav(target, names)


def test_export_refuses_a_broken_file_and_can_omit_long_texts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sav_analytics.core import sav_export
    from sav_analytics.core.sav_reader import inspect_sav
    from sav_analytics.core.sav_writing import SavWriteMismatchError

    source = tmp_path / "long.sav"
    pyreadstat.write_sav(
        pd.DataFrame({"ID": [1.0, 2.0], "COMMENT": ["я" * 200, "коротко"]}), source
    )
    inspection = inspect_sav(source).to_dict()
    project = {
        "inspection": inspection,
        "configuration": {"questions": inspection["questions"], "recodings": []},
    }
    real_verify = sav_export.verify_written_sav

    def broken(path, expected):
        # Как на реальном массиве: с длинным текстом файл выходит испорченным.
        if "COMMENT" in expected:
            raise SavWriteMismatchError("лишняя переменная")
        real_verify(path, expected)

    monkeypatch.setattr(sav_export, "verify_written_sav", broken)
    target = tmp_path / "out.sav"
    with pytest.raises(sav_export.SavExportError, match="без длинных текстов") as caught:
        sav_export.export_project_sav(source, project, target)
    assert caught.value.long_text == ["COMMENT"]

    summary = sav_export.export_project_sav(source, project, target, omit_long_text=True)
    assert summary.omitted == ["COMMENT"]
    _, meta = pyreadstat.read_sav(target, metadataonly=True)
    assert meta.column_names == ["ID"]
    assert "COMMENT" in " ".join(meta.notes)
