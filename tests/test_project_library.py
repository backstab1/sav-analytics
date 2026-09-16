"""Библиотека проектов: переименование, копия, корзина и восстановление."""

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture


def test_project_is_renamed_copied_trashed_and_restored(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            created = client.post(
                "/api/projects",
                data={"name": "Бренд"},
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()
            project_id = created["id"]

            renamed = client.patch(f"/api/projects/{project_id}", json={"name": "  Трекер  "})
            assert renamed.status_code == 200
            assert renamed.json()["name"] == "Трекер"
            blank = client.patch(f"/api/projects/{project_id}", json={"name": "   "})
            assert blank.status_code == 422

            copy = client.post(f"/api/projects/{project_id}/duplicate")
            assert copy.status_code == 201
            duplicate = copy.json()
            assert duplicate["id"] != project_id
            assert duplicate["name"] == "Копия — Трекер"
            assert duplicate["source"]["sha256"] == created["source"]["sha256"]
            assert duplicate["configuration"]["revision"] == 1
            assert client.get(f"/api/projects/{duplicate['id']}/source").status_code == 200
            listed = {item["id"] for item in client.get("/api/projects").json()}
            assert listed == {project_id, duplicate["id"]}

            assert client.delete(f"/api/projects/{duplicate['id']}").status_code == 200
            assert [item["id"] for item in client.get("/api/projects").json()] == [project_id]
            assert client.get(f"/api/projects/{duplicate['id']}").status_code == 404
            trash = client.get("/api/projects/trash").json()
            assert [item["id"] for item in trash] == [duplicate["id"]]
            assert trash[0]["trashed_at"]

            restored = client.post(f"/api/projects/trash/{duplicate['id']}/restore")
            assert restored.status_code == 200
            assert restored.json()["name"] == "Копия — Трекер"
            assert client.get("/api/projects/trash").json() == []

            assert client.delete(f"/api/projects/{uuid4()}").status_code == 404
            assert client.post(f"/api/projects/trash/{uuid4()}/restore").status_code == 404
    finally:
        app.dependency_overrides.clear()
