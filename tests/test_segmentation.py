"""Сегментация k-means как перекодировка проекта (PQ.11)."""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.segmentation import segment_series, silhouette
from sav_analytics.repository import ProjectRepository

CENTRES = [(0.0, 0.0), (8.0, 8.0), (0.0, 8.0)]
SEGMENTS = {"mode": "segments", "code": "SEG", "name": "Сегменты", "variables": ["A", "B"], "k": 3}


def _blobs(size: int = 60) -> pd.DataFrame:
    generator = np.random.default_rng(5)
    rows = []
    for cluster, (x, y) in enumerate(CENTRES, start=1):
        for _ in range(size):
            rows.append((x + generator.normal(0, 0.6), y + generator.normal(0, 0.6), cluster))
    frame = pd.DataFrame(rows, columns=["A", "B", "TRUE"])
    frame["SEX"] = np.tile([1, 2], len(frame) // 2)
    return frame


def _write(path: Path, frame: pd.DataFrame) -> None:
    pyreadstat.write_sav(
        frame.round(3),
        path,
        column_labels={"A": "Цена важна", "B": "Качество важно", "TRUE": "Кластер", "SEX": "Пол"},
        variable_value_labels={"SEX": {1: "Мужчина", 2: "Женщина"}},
        variable_measure={"A": "scale", "B": "scale", "TRUE": "nominal", "SEX": "nominal"},
    )


def _silhouette_by_hand(points: list[tuple[float, float]], labels: list[int]) -> float:
    """Силуэт по определению, без numpy: эталон для векторного расчёта."""
    def distance(p: tuple[float, float], q: tuple[float, float]) -> float:
        return math.dist(p, q)

    scores = []
    for index, point in enumerate(points):
        same = [q for j, q in enumerate(points) if labels[j] == labels[index] and j != index]
        inner = sum(distance(point, q) for q in same) / len(same)
        outer = min(
            sum(distance(point, q) for j, q in enumerate(points) if labels[j] == other)
            / labels.count(other)
            for other in set(labels)
            if other != labels[index]
        )
        scores.append((outer - inner) / max(inner, outer))
    return sum(scores) / len(scores)


def test_silhouette_matches_the_definition() -> None:
    generator = np.random.default_rng(1)
    points = generator.normal(size=(30, 2))
    labels = np.array([0] * 10 + [1] * 10 + [2] * 10)

    expected = _silhouette_by_hand([tuple(row) for row in points], labels.tolist())

    assert silhouette(points, labels) == pytest.approx(expected)


def _client(tmp_path: Path, frame: pd.DataFrame) -> tuple[TestClient, str]:
    source = tmp_path / "blobs.sav"
    _write(source, frame)
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    client = TestClient(app)
    with source.open("rb") as stream:
        project_id = client.post(
            "/api/projects", files={"file": ("blobs.sav", stream, "application/octet-stream")}
        ).json()["id"]
    return client, project_id


def test_separated_groups_are_found_and_become_a_banner_block(tmp_path: Path) -> None:
    frame = _blobs()
    client, project_id = _client(tmp_path, frame)
    try:
        suggested = client.post(
            f"/api/projects/{project_id}/recodings/segments/suggest",
            json={"variables": ["A", "B"], "k_min": 2, "k_max": 5},
        )
        assert suggested.status_code == 200, suggested.text
        assert suggested.json()["best"] == 3

        created = client.post(
            f"/api/projects/{project_id}/recodings",
            json=SEGMENTS,
        )
        assert created.status_code == 201, created.text
        project = created.json()
        recoding = project["configuration"]["recodings"][0]
        assert [item["label"] for item in recoding["categories"]] == [
            "Сегмент 1",
            "Сегмент 2",
            "Сегмент 3",
        ]
        assert recoding["model"]["base"] == 180

        # Каждый настоящий кластер целиком попал в один сегмент.
        labels = segment_series(recoding, project, frame)
        purity = pd.crosstab(frame["TRUE"], labels)
        assert (purity.max(axis=1) == 60).all()

        # Тот же расчёт на тех же данных даёт те же центры.
        again = client.put(
            f"/api/projects/{project_id}/recodings/{recoding['id']}",
            json=SEGMENTS,
        ).json()["configuration"]["recodings"][0]
        assert again["model"]["centres"] == recoding["model"]["centres"]

        banner = client.post(
            f"/api/projects/{project_id}/banners",
            json={
                "name": "По сегментам",
                "blocks": [{"sources": [{"kind": "recoding", "ref": recoding["id"]}]}],
            },
        )
        assert banner.status_code == 201, banner.text
        banner_id = banner.json()["configuration"]["banners"][0]["id"]
        preview = client.get(f"/api/projects/{project_id}/banners/{banner_id}/preview").json()
        assert [column["base"] for column in preview["columns"][1:]] == [60, 60, 60]

        blocked = client.delete(f"/api/projects/{project_id}/questions/A")
        assert blocked.status_code in {404, 405, 422}
        refused = client.post(
            f"/api/projects/{project_id}/questions/group",
            json={"codes": ["A", "B"], "question_type": "matrix", "code": "AB", "label": "AB"},
        )
        assert refused.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_new_data_is_assigned_to_the_stored_centres(tmp_path: Path) -> None:
    frame = _blobs()
    client, project_id = _client(tmp_path, frame)
    try:
        project = client.post(
            f"/api/projects/{project_id}/recodings",
            json=SEGMENTS,
        ).json()
        recoding = project["configuration"]["recodings"][0]
        reference = segment_series(recoding, project, frame)
        centre_label = {
            cluster: reference[frame["TRUE"] == cluster].iloc[0] for cluster in (1, 2, 3)
        }

        wave = pd.DataFrame({"A": [0.2, 7.9, 0.1, None], "B": [0.1, 8.2, 7.7, 1.0]})
        assigned = segment_series(recoding, project, wave)

        assert assigned.iloc[:3].tolist() == [centre_label[1], centre_label[2], centre_label[3]]
        assert pd.isna(assigned.iloc[3])
    finally:
        app.dependency_overrides.clear()


def test_segmentation_refuses_categorical_variables_and_small_bases(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path, _blobs(size=4))
    try:
        categorical = client.post(
            f"/api/projects/{project_id}/recodings",
            json={"mode": "segments", "code": "SEG", "name": "С", "variables": ["SEX"], "k": 2},
        )
        assert categorical.status_code == 422
        assert "числовым" in categorical.json()["detail"]

        small = client.post(
            f"/api/projects/{project_id}/recodings",
            json={"mode": "segments", "code": "SEG", "name": "С", "variables": ["A", "B"], "k": 3},
        )
        assert small.status_code == 422
        assert "не меньше 30" in small.json()["detail"]
    finally:
        app.dependency_overrides.clear()
