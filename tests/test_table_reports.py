"""Несколько сохранённых таблиц в разделе «Таблицы» (PQ.7 для экрана кросстаба)."""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture

RECODING = {
    "code": "SCORE_GROUP",
    "name": "Группы оценки",
    "source_variable": "Q2",
    "categories": [
        {"label": "Низкая", "lower": 0, "upper": 7},
        {"label": "Высокая", "lower": 8, "upper": 10},
    ],
}
FILTER = {
    "name": "Мужчины",
    "rule": {
        "operator": "and",
        "items": [
            {"source": {"kind": "question", "ref": "Q1"}, "operator": "eq", "values": [1]}
        ],
    },
}


@pytest.fixture
def project(tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
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
            yield client, project_id
    finally:
        app.dependency_overrides.pop(get_repository, None)


def _reports(response) -> list[dict]:
    assert response.status_code in {200, 201}, response.text
    return response.json()["configuration"]["table_reports"]


def test_tables_are_created_renamed_copied_cleared_and_deleted(project) -> None:
    client, project_id = project
    base = f"/api/projects/{project_id}/tables/reports"

    first = _reports(client.post(base, json={"rows": ["Q1"]}))[0]
    assert first["name"] == "Таблица 1"
    assert first["rows"] == ["Q1"]
    assert first["sheet"] == "main" and first["measure"] == "value"

    reports = _reports(client.post(base, json={"rows": ["Q2"], "cols": [
        {"sources": [{"kind": "question", "ref": "Q1"}]}], "measure": "index"}))
    assert [item["name"] for item in reports] == ["Таблица 1", "Таблица 2"]
    second = reports[1]
    assert second["cols"] == [{"sources": [{"kind": "question", "ref": "Q1"}]}]

    renamed = _reports(client.patch(f"{base}/{first['id']}", json={"name": "  Пол  "}))
    assert renamed[0]["name"] == "Пол"

    copied = _reports(client.post(f"{base}/{first['id']}/copy"))
    assert [item["name"] for item in copied] == ["Пол", "Пол (копия)", "Таблица 2"]
    assert copied[1]["rows"] == ["Q1"] and copied[1]["id"] != first["id"]
    again = _reports(client.post(f"{base}/{first['id']}/copy"))
    assert again[1]["name"] == "Пол (копия 2)"

    # «Очистить» — запись пустой раскладки: название остаётся.
    cleared = _reports(client.put(f"{base}/{second['id']}", json={}))
    assert cleared[-1]["name"] == "Таблица 2" and cleared[-1]["rows"] == []

    remaining = _reports(client.delete(f"{base}/{first['id']}"))
    assert first["id"] not in {item["id"] for item in remaining}
    # Номер освободился — следующая безымянная таблица его и займёт.
    assert _reports(client.post(base, json={}))[-1]["name"] == "Таблица 1"


def test_layout_keeps_view_and_nets(project) -> None:
    client, project_id = project
    base = f"/api/projects/{project_id}/tables/reports"
    report = _reports(client.post(base, json={}))[0]
    layout = {
        "rows": ["Q1", "Q2"],
        "cols": [
            {"sources": [{"kind": "question", "ref": "Q1"}, {"kind": "question", "ref": "Q2"}]},
            {"sources": [{"kind": "question", "ref": "Q1"}]},
        ],
        "sheet": "filter",
        "scale_box": 3,
        "boxes": {"Q2": 1},
        "inverted": ["Q2"],
        "nets": {"Q1": [{"label": "Все", "values": ["Мужчина", "Женщина"]}]},
    }
    saved = _reports(client.put(f"{base}/{report['id']}", json=layout))[0]
    assert [len(block["sources"]) for block in saved["cols"]] == [2, 1]
    assert saved["cols"][0]["sources"][1]["ref"] == "Q2" and saved["sheet"] == "filter"
    assert saved["scale_box"] == 3 and saved["boxes"] == {"Q2": 1}
    assert saved["inverted"] == ["Q2"]
    assert saved["nets"]["Q1"][0]["label"] == "Все"
    assert saved["name"] == "Таблица 1"


def test_layout_is_checked_against_the_project(project) -> None:
    client, project_id = project
    base = f"/api/projects/{project_id}/tables/reports"

    missing = client.post(base, json={"rows": ["NOPE"]})
    assert missing.status_code == 422
    assert "NOPE" in missing.json()["detail"]
    assert client.post(base, json={"boxes": {"NOPE": 2}}).status_code == 422
    assert client.post(base, json={"boxes": {"Q1": 4}}).status_code == 422
    both = client.post(base, json={
        "banner_id": "00000000-0000-0000-0000-000000000001",
        "cols": [{"sources": [{"kind": "question", "ref": "Q1"}]}],
    })
    assert both.status_code == 422
    # В блоке одна или две переменные: третий уровень не собирается.
    three = client.post(base, json={"cols": [{"sources": [
        {"kind": "question", "ref": "Q1"}] * 3}]})
    assert three.status_code == 422
    unknown_banner = client.post(base, json={"banner_id": "00000000-0000-0000-0000-000000000001"})
    assert unknown_banner.status_code == 422
    assert client.put(f"{base}/00000000-0000-0000-0000-000000000002", json={}).status_code == 404


def test_saved_tables_protect_what_they_use(project) -> None:
    client, project_id = project
    base = f"/api/projects/{project_id}/tables/reports"
    recoding_id = client.post(
        f"/api/projects/{project_id}/recodings", json=RECODING
    ).json()["configuration"]["recodings"][0]["id"]
    filter_id = client.post(
        f"/api/projects/{project_id}/filters", json=FILTER
    ).json()["configuration"]["filters"][0]["id"]
    _reports(client.post(base, json={
        "name": "По оценке",
        "rows": ["Q1"],
        "cols": [{"sources": [
            {"kind": "question", "ref": "Q1"}, {"kind": "recoding", "ref": recoding_id}
        ]}],
        "filter_id": filter_id,
    }))

    blocked = client.delete(f"/api/projects/{project_id}/recodings/{recoding_id}")
    assert blocked.status_code in {409, 422}
    assert "таблица «По оценке»" in blocked.json()["detail"]
    blocked = client.delete(f"/api/projects/{project_id}/filters/{filter_id}")
    assert blocked.status_code in {409, 422}
    assert "таблица «По оценке»" in blocked.json()["detail"]


def test_deleted_banner_leaves_the_table_with_total_only(project) -> None:
    client, project_id = project
    base = f"/api/projects/{project_id}/tables/reports"
    banner_id = client.post(
        f"/api/projects/{project_id}/banners",
        json={"name": "Пол", "blocks": [{"sources": [{"kind": "question", "ref": "Q1"}]}]},
    ).json()["configuration"]["banners"][0]["id"]
    _reports(client.post(base, json={"rows": ["Q2"], "banner_id": banner_id}))

    after = _reports(client.delete(f"/api/projects/{project_id}/banners/{banner_id}"))
    assert after[0]["banner_id"] is None
    assert after[0]["rows"] == ["Q2"]


def test_a_series_of_layout_edits_is_one_undo_step(project) -> None:
    client, project_id = project
    base = f"/api/projects/{project_id}/tables/reports"
    report = _reports(client.post(base, json={}))[0]
    for rows in (["Q1"], ["Q1", "Q2"], ["Q2"]):
        _reports(client.put(f"{base}/{report['id']}", json={"rows": rows}))

    history = client.get(f"/api/projects/{project_id}/history").json()
    assert history["undo"] == 2  # создание таблицы и серия правок её раскладки
    assert history["undo_sections"] == ["таблицы"]

    undone = _reports(client.post(f"/api/projects/{project_id}/undo"))
    assert undone[0]["rows"] == []
    redone = _reports(client.post(f"/api/projects/{project_id}/redo"))
    assert redone[0]["rows"] == ["Q2"]

    # Правка другой таблицы — уже новый шаг.
    other = _reports(client.post(base, json={}))[1]
    _reports(client.put(f"{base}/{other['id']}", json={"rows": ["Q1"]}))
    _reports(client.put(f"{base}/{report['id']}", json={"rows": ["Q1"]}))
    assert client.get(f"/api/projects/{project_id}/history").json()["undo"] == 5


def test_schema_2_columns_become_explicit_blocks(tmp_path: Path) -> None:
    """Флаг вложенности схемы 2 делил колонки на пары по порядку: миграция
    сохраняет ровно этот разрез, но уже явными блоками."""
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    with source.open("rb") as stream:
        created = repository.create("Схема 2", "fixture.sav", stream)
    legacy = repository.stored_document(created["id"])
    legacy["configuration"]["schema_version"] = 2
    q1, q2 = ({"kind": "question", "ref": code} for code in ("Q1", "Q2"))
    legacy["configuration"]["table_reports"] = [
        {"id": str(uuid4()), "name": "Рядом", "rows": [], "cols": [q1, q2], "nested": False},
        {"id": str(uuid4()), "name": "Пары", "rows": [], "cols": [q1, q2, q1], "nested": True},
        {"id": str(uuid4()), "name": "Пустая", "rows": []},
    ]
    repository.overwrite_stored_document(created["id"], legacy)

    reports = repository.get(UUID(created["id"]))["configuration"]["table_reports"]

    assert reports[0]["cols"] == [{"sources": [q1]}, {"sources": [q2]}]
    # Непарный хвост и раньше шёл отдельным блоком.
    assert reports[1]["cols"] == [{"sources": [q1, q2]}, {"sources": [q1]}]
    assert reports[2]["cols"] == []
    assert all("nested" not in report for report in reports)
    assert [item["schema_version"] for item in repository.metadata.backups(created["id"])] == [2]
