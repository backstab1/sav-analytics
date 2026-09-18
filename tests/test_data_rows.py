"""Локальный просмотр строк: пагинация, подписи и сохранённый фильтр."""

from pathlib import Path

from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.data_rows import DataRowsError, browse_data_rows
from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture


def _project(source: Path) -> dict:
    inspection = inspect_sav(source).to_dict()
    return {
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [],
            "filters": [
                {
                    "id": "women",
                    "name": "Только женщины",
                    "rule": {
                        "kind": "group",
                        "operator": "and",
                        "items": [
                            {
                                "kind": "condition",
                                "source": {"kind": "question", "ref": "Q1"},
                                "operator": "in",
                                "values": [2],
                            }
                        ],
                    },
                }
            ],
        },
    }


def test_rows_keep_source_numbers_labels_filter_and_pagination(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    page = browse_data_rows(
        source,
        _project(source),
        columns=["id", "Q1", "Q2"],
        filter_id="women",
        offset=1,
        limit=10,
    )

    assert (page["source_total"], page["total"]) == (4, 2)
    assert [item["name"] for item in page["columns"]] == ["id", "Q1", "Q2"]
    assert [row["number"] for row in page["rows"]] == [4]
    assert page["rows"][0]["values"][0]["raw"] == 104.0
    assert page["rows"][0]["values"][1] == {
        "raw": 2.0,
        "display": "Женщина",
        "label": "Женщина",
        "truncated": False,
    }
    assert page["rows"][0]["values"][2]["display"] == "—"


def test_rows_reject_unknown_columns_filter_and_wide_request(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = _project(source)
    for kwargs, message in (
        ({"columns": ["UNKNOWN"]}, "не найдены"),
        ({"filter_id": "missing"}, "Фильтр"),
        ({"columns": [f"Q{index}" for index in range(26)]}, "25"),
    ):
        try:
            browse_data_rows(source, project, **kwargs)
        except DataRowsError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("Ожидалась ошибка запроса строк")


def test_rows_endpoint_is_read_only_and_validated(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects", files={"file": ("fixture.sav", stream)}
            ).json()
            project_id = project["id"]
            definition = dict(_project(source)["configuration"]["filters"][0])
            definition.pop("id")
            created = repository.create_filter(project_id, definition)
            filter_id = created["configuration"]["filters"][-1]["id"]
            response = client.get(
                f"/api/projects/{project_id}/data/rows",
                params=[("columns", "id"), ("columns", "Q1"), ("filter_id", filter_id)],
            )
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 2
            assert repository.get(project_id)["configuration"]["revision"] == 2
            assert client.get(
                f"/api/projects/{project_id}/data/rows", params={"limit": 101}
            ).status_code == 422
    finally:
        app.dependency_overrides.clear()
