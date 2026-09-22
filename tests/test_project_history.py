"""«Отменить» и «Вернуть» в проекте (P2, GAP-006)."""

from pathlib import Path

from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    client = TestClient(app)
    with source.open("rb") as stream:
        project_id = client.post(
            "/api/projects", files={"file": ("research.sav", stream, "application/octet-stream")}
        ).json()["id"]
    return client, project_id


def _label(project: dict, code: str) -> str:
    return next(item for item in project["configuration"]["questions"] if item["code"] == code)[
        "label"
    ]


def test_change_is_undone_and_redone_as_new_revisions(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path)
    try:
        original = client.get(f"/api/projects/{project_id}").json()
        assert client.get(f"/api/projects/{project_id}/history").json()["undo"] == 0

        edited = client.patch(
            f"/api/projects/{project_id}/questions/Q1", json={"label": "Пол респондента"}
        ).json()
        history = client.get(f"/api/projects/{project_id}/history").json()
        assert history["undo"] == 1
        assert history["undo_sections"] == ["структура вопросов"]

        undone = client.post(f"/api/projects/{project_id}/undo")
        assert undone.status_code == 200
        undone = undone.json()
        assert _label(undone, "Q1") == _label(original, "Q1")
        # Отмена — новая ревизия, а не откат номера: кэш и блокировки не путаются.
        assert undone["configuration"]["revision"] == edited["configuration"]["revision"] + 1
        assert client.get(f"/api/projects/{project_id}/history").json()["redo"] == 1

        redone = client.post(f"/api/projects/{project_id}/redo").json()
        assert _label(redone, "Q1") == "Пол респондента"
        history = client.get(f"/api/projects/{project_id}/history").json()
        assert (history["undo"], history["redo"]) == (1, 0)
    finally:
        app.dependency_overrides.clear()


def test_new_change_clears_redo_and_empty_undo_is_refused(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path)
    try:
        refused = client.post(f"/api/projects/{project_id}/undo")
        assert refused.status_code == 422
        assert "нечего" in refused.json()["detail"]

        client.patch(f"/api/projects/{project_id}/questions/Q1", json={"label": "Первое"})
        client.post(f"/api/projects/{project_id}/undo")
        client.put(f"/api/projects/{project_id}/report-settings", json={"minimum_base": 40})

        history = client.get(f"/api/projects/{project_id}/history").json()
        assert history["redo"] == 0
        assert history["undo_sections"] == ["настройки отчёта"]
    finally:
        app.dependency_overrides.clear()


def test_undo_respects_the_revision_of_the_caller(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path)
    try:
        edited = client.patch(
            f"/api/projects/{project_id}/questions/Q1", json={"label": "Первое"}
        ).json()
        stale = edited["configuration"]["revision"] - 1

        conflict = client.post(
            f"/api/projects/{project_id}/undo", headers={"If-Match": str(stale)}
        )

        assert conflict.status_code == 409
        assert client.get(f"/api/projects/{project_id}/history").json()["undo"] == 1
    finally:
        app.dependency_overrides.clear()


def test_new_wave_starts_the_history_again(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path)
    try:
        client.patch(f"/api/projects/{project_id}/questions/Q1", json={"label": "Первое"})
        wave = tmp_path / "wave.sav"
        write_fixture(wave)
        with wave.open("rb") as stream:
            replaced = client.put(
                f"/api/projects/{project_id}/source",
                files={"file": ("wave.sav", stream, "application/octet-stream")},
            )
        assert replaced.status_code == 200

        history = client.get(f"/api/projects/{project_id}/history").json()
        assert (history["undo"], history["redo"]) == (0, 0)
    finally:
        app.dependency_overrides.clear()
