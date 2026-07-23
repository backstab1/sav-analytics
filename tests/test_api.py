from pathlib import Path

from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture


def test_create_project_keeps_source_and_returns_inspection(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            response = client.post(
                "/api/projects",
                data={"name": "Тестовый проект"},
                files={"file": ("research.sav", stream, "application/octet-stream")},
            )
            assert response.status_code == 201
            project = response.json()
            assert project["name"] == "Тестовый проект"
            assert project["inspection"]["row_count"] == 4
            assert repository.source_path(project["id"]).read_bytes() == source.read_bytes()
    finally:
        app.dependency_overrides.clear()


def test_rejects_non_sav_extension(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=1024)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/projects", files={"file": ("data.csv", b"a,b\n1,2", "text/csv")}
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_rejects_corrupted_sav_with_json_error(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=1024)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/projects",
                files={"file": ("broken.sav", b"not an spss file", "application/octet-stream")},
            )
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/json")
        assert "SPSS SAV" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_question_configuration_and_preview_are_persisted(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                data={"name": "Настройка"},
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()
            project_id = project["id"]

            updated = client.patch(
                f"/api/projects/{project_id}/questions/Q1",
                json={"label": "Пол респондента", "included_in_report": False},
            )
            assert updated.status_code == 200
            q1 = next(
                item
                for item in updated.json()["configuration"]["questions"]
                if item["code"] == "Q1"
            )
            assert q1["label"] == "Пол респондента"
            assert not q1["included_in_report"]

            preview = client.get(f"/api/projects/{project_id}/questions/Q1/preview")
            assert preview.status_code == 200
            assert preview.json()["total_base"] == 4
            assert preview.json()["valid_base"] == 4
            assert [row["count"] for row in preview.json()["rows"]] == [2, 2]

            codes = [item["code"] for item in project["configuration"]["questions"]]
            reordered = client.put(
                f"/api/projects/{project_id}/questions/order",
                json={"codes": list(reversed(codes))},
            )
            assert reordered.status_code == 200
            assert reordered.json()["configuration"]["questions"][0]["code"] == codes[-1]

            persisted = client.get(f"/api/projects/{project_id}").json()
            assert persisted["configuration"]["questions"][0]["code"] == codes[-1]
    finally:
        app.dependency_overrides.clear()
