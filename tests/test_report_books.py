"""Несколько книг отчёта в проекте (PQ.7): у каждой свои баннер, фильтр и настройки."""

import io
import json
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.report import build_statistics_txt
from sav_analytics.core.report_books import configuration_for_book
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture
from tests.test_table_reports import FILTER

BANNER = {"name": "Пол", "blocks": [{"sources": [{"kind": "question", "ref": "Q1"}]}]}


@pytest.fixture
def project(tmp_path: Path) -> Iterator[tuple[TestClient, str, ProjectRepository]]:
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
            yield client, project_id, repository
    finally:
        app.dependency_overrides.pop(get_repository, None)


def _configuration(response) -> dict:
    assert response.status_code in {200, 201}, response.text
    return response.json()["configuration"]


def _settings(client: TestClient, project_id: str, **changes: object) -> dict:
    current = client.get(f"/api/projects/{project_id}").json()["configuration"]
    payload = {**current["report_settings"], **changes}
    return _configuration(client.put(f"/api/projects/{project_id}/report-settings", json=payload))


def test_new_project_has_one_book(project) -> None:
    client, project_id, _ = project
    configuration = client.get(f"/api/projects/{project_id}").json()["configuration"]

    assert [book["name"] for book in configuration["reports"]] == ["Отчёт"]
    assert configuration["active_report_id"] == configuration["reports"][0]["id"]
    assert "settings" not in configuration["reports"][0]


def test_books_keep_their_own_banner_filter_and_settings(project) -> None:
    client, project_id, _ = project
    base = f"/api/projects/{project_id}/report-books"
    banner_id = _configuration(client.post(f"/api/projects/{project_id}/banners", json=BANNER))[
        "banners"
    ][0]["id"]
    filter_id = _configuration(client.post(f"/api/projects/{project_id}/filters", json=FILTER))[
        "filters"
    ][0]["id"]
    _configuration(
        client.put(f"/api/projects/{project_id}/report-filter", json={"filter_id": filter_id})
    )
    first = _settings(client, project_id, confidence_level=0.9, show_counts=True)
    first_id = first["active_report_id"]
    assert first["report_banner_id"] == banner_id

    created = _configuration(client.post(base, json={"name": "Для клиента"}))
    second_id = created["active_report_id"]
    assert second_id != first_id
    # Новая книга пустая: без баннера и фильтра, настройки по умолчанию.
    assert created["report_banner_id"] is None
    assert created["report_filter_id"] is None
    assert created["report_settings"]["confidence_level"] == 0.95
    assert created["report_settings"]["show_counts"] is False
    stored_first = next(book for book in created["reports"] if book["id"] == first_id)
    assert stored_first["settings"]["confidence_level"] == 0.9
    assert stored_first["banner_id"] == banner_id
    _settings(client, project_id, minimum_base=50)

    back = _configuration(client.post(f"{base}/{first_id}/activate"))
    assert back["active_report_id"] == first_id
    assert back["report_settings"]["confidence_level"] == 0.9
    assert back["report_banner_id"] == banner_id
    assert back["report_filter_id"] == filter_id
    stored_second = next(book for book in back["reports"] if book["id"] == second_id)
    assert stored_second["settings"]["minimum_base"] == 50
    assert "settings" not in next(book for book in back["reports"] if book["id"] == first_id)


def test_books_are_copied_renamed_cleared_and_deleted(project) -> None:
    client, project_id, _ = project
    base = f"/api/projects/{project_id}/report-books"
    _settings(client, project_id, confidence_level=0.99)
    first_id = client.get(f"/api/projects/{project_id}").json()["configuration"]["active_report_id"]

    copied = _configuration(client.post(base, json={"source_id": first_id}))
    assert [book["name"] for book in copied["reports"]] == ["Отчёт", "Отчёт (копия)"]
    assert copied["report_settings"]["confidence_level"] == 0.99
    copy_id = copied["active_report_id"]

    renamed = _configuration(client.patch(f"{base}/{copy_id}", json={"name": "  Без   фильтра "}))
    assert renamed["reports"][1]["name"] == "Без фильтра"

    cleared = _configuration(client.post(f"{base}/{copy_id}/clear"))
    assert cleared["report_settings"]["confidence_level"] == 0.95

    deleted = _configuration(client.delete(f"{base}/{copy_id}"))
    assert [book["id"] for book in deleted["reports"]] == [first_id]
    assert deleted["active_report_id"] == first_id
    assert deleted["report_settings"]["confidence_level"] == 0.99

    last = client.delete(f"{base}/{first_id}")
    assert last.status_code == 422
    assert "Единственную книгу" in last.json()["detail"]
    assert client.post(f"{base}/00000000-0000-0000-0000-000000000000/activate").status_code == 404
    numbered = _configuration(client.post(base, json={}))
    assert numbered["reports"][-1]["name"] == "Отчёт 2"


def test_inactive_book_protects_its_filter_and_weight_and_loses_a_deleted_banner(project) -> None:
    client, project_id, _ = project
    base = f"/api/projects/{project_id}/report-books"
    banner_id = _configuration(client.post(f"/api/projects/{project_id}/banners", json=BANNER))[
        "banners"
    ][0]["id"]
    filter_id = _configuration(client.post(f"/api/projects/{project_id}/filters", json=FILTER))[
        "filters"
    ][0]["id"]
    _configuration(
        client.put(f"/api/projects/{project_id}/report-filter", json={"filter_id": filter_id})
    )
    _configuration(client.post(base, json={"name": "Вторая"}))

    blocked = client.delete(f"/api/projects/{project_id}/filters/{filter_id}")
    assert blocked.status_code in {409, 422}
    assert "книги «Отчёт»" in blocked.json()["detail"]

    after = _configuration(client.delete(f"/api/projects/{project_id}/banners/{banner_id}"))
    first = next(book for book in after["reports"] if book["name"] == "Отчёт")
    assert first["banner_id"] is None


def test_every_book_action_is_one_undo_step(project) -> None:
    client, project_id, _ = project
    base = f"/api/projects/{project_id}/report-books"
    first_id = client.get(f"/api/projects/{project_id}").json()["configuration"]["active_report_id"]
    _configuration(client.post(base, json={"name": "Вторая"}))

    undone = _configuration(client.post(f"/api/projects/{project_id}/undo"))
    assert [book["id"] for book in undone["reports"]] == [first_id]
    assert undone["active_report_id"] == first_id
    history = client.get(f"/api/projects/{project_id}/history").json()
    assert "книги отчёта" in history["redo_sections"]


def test_inactive_book_builds_with_its_own_settings(project) -> None:
    client, project_id, repository = project
    base = f"/api/projects/{project_id}/report-books"
    _settings(client, project_id, confidence_level=0.9)
    first_id = client.get(f"/api/projects/{project_id}").json()["configuration"]["active_report_id"]
    _configuration(client.post(base, json={"name": "Строгая"}))
    _settings(client, project_id, confidence_level=0.99)

    project_data = repository.get(UUID(project_id))
    source = repository.source_path(UUID(project_id))
    first = {
        **project_data,
        "configuration": configuration_for_book(project_data["configuration"], first_id),
    }

    from sav_analytics.core.reporting.statistics import audit_number

    strict = build_statistics_txt(source, project_data)
    loose = build_statistics_txt(source, first)
    assert f"Уровень доверия: {audit_number(99.0)}%" in strict
    assert f"Уровень доверия: {audit_number(90.0)}%" in loose
    # Копия не трогает сохранённый проект.
    assert project_data["configuration"]["report_settings"]["confidence_level"] == 0.99


def test_schema_3_project_gets_its_configured_book(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    with source.open("rb") as stream:
        created = repository.create("Схема 3", "fixture.sav", stream)
    metadata_path = tmp_path / "projects" / created["id"] / "project.json"
    legacy = json.loads(metadata_path.read_text(encoding="utf-8"))
    legacy["configuration"]["schema_version"] = 3
    legacy["configuration"].pop("reports")
    legacy["configuration"].pop("active_report_id")
    legacy["configuration"]["report_settings"]["confidence_level"] = 0.9
    metadata_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    configuration = repository.get(UUID(created["id"]))["configuration"]

    assert configuration["schema_version"] == 4
    assert [book["name"] for book in configuration["reports"]] == ["Отчёт"]
    assert configuration["active_report_id"] == configuration["reports"][0]["id"]
    assert configuration["report_settings"]["confidence_level"] == 0.9
    assert metadata_path.with_suffix(".v3.bak").is_file()
    # Идентификатор книги записан: повторное чтение его не меняет.
    again = repository.get(UUID(created["id"]))["configuration"]
    assert again["active_report_id"] == configuration["active_report_id"]


EMPTY_FILTER = {
    "name": "Никто",
    "rule": {
        "operator": "and",
        "items": [
            {"source": {"kind": "question", "ref": "Q1"}, "operator": "eq", "values": [1]},
            {"source": {"kind": "question", "ref": "Q1"}, "operator": "eq", "values": [2]},
        ],
    },
}


def _wait(client: TestClient, project_id: str, job: dict) -> dict:
    deadline = time.monotonic() + 30
    while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.05)
        job = client.get(f"/api/projects/{project_id}/reports/jobs/{job['job_id']}").json()
    return job


def test_all_books_build_at_once_without_changing_the_selected_one(project) -> None:
    client, project_id, _ = project
    base = f"/api/projects/{project_id}/report-books"
    _settings(client, project_id, confidence_level=0.9)
    _configuration(client.post(base, json={"name": "Строгая"}))
    _settings(client, project_id, confidence_level=0.99)
    filter_id = _configuration(
        client.post(f"/api/projects/{project_id}/filters", json=EMPTY_FILTER)
    )["filters"][0]["id"]
    _configuration(client.post(base, json={"name": "Пустая"}))
    _configuration(
        client.put(f"/api/projects/{project_id}/report-filter", json={"filter_id": filter_id})
    )
    strict_id = next(
        book["id"]
        for book in client.get(f"/api/projects/{project_id}").json()["configuration"]["reports"]
        if book["name"] == "Строгая"
    )
    selected = _configuration(client.post(f"{base}/{strict_id}/activate"))

    response = client.post(f"/api/projects/{project_id}/reports/prepare-all")
    assert response.status_code == 200, response.text
    books = {book["book"]: book for book in response.json()["books"]}

    assert list(books) == ["Отчёт", "Строгая", "Пустая"]
    assert books["Пустая"]["status"] == "blocked"
    assert books["Пустая"]["preflight"]["can_prepare"] is False
    assert books["Строгая"]["active"] is True
    done = {name: _wait(client, project_id, books[name]) for name in ("Отчёт", "Строгая")}
    assert {job["status"] for job in done.values()} == {"complete"}
    assert done["Отчёт"]["artifact_id"] != done["Строгая"]["artifact_id"]

    # Выбранная книга и ревизия не изменились: сборка шла на копиях.
    after = client.get(f"/api/projects/{project_id}").json()["configuration"]
    assert after["active_report_id"] == strict_id
    assert after["revision"] == selected["revision"]
    # Сборка выбранной книги совпадает с обычной и берётся из кэша.
    again = client.post(f"/api/projects/{project_id}/reports/prepare").json()
    assert again["cached"] is True
    assert again["artifact_id"] == done["Строгая"]["artifact_id"]

    # Файл не выбранной книги называется по ней, а не по выбранной.
    loose = client.get(done["Отчёт"]["downloads"]["topline"])
    assert "_%D0%9E%D1%82%D1%87%D1%91%D1%82_topline.xlsx" in loose.headers["content-disposition"]

    bundle = client.get(
        f"/api/projects/{project_id}/reports/bundle.zip",
        params=[("artifact", done[name]["artifact_id"]) for name in ("Отчёт", "Строгая")],
    )
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert sorted(archive.namelist()) == [
            "Отчёт_statistics.txt",
            "Отчёт_topline.xlsx",
            "Строгая_statistics.txt",
            "Строгая_topline.xlsx",
        ]
        from sav_analytics.core.reporting.statistics import audit_number

        loose_text = archive.read("Отчёт_statistics.txt").decode("utf-8")
        strict_text = archive.read("Строгая_statistics.txt").decode("utf-8")
    assert f"Уровень доверия: {audit_number(90.0)}%" in loose_text
    assert f"Уровень доверия: {audit_number(99.0)}%" in strict_text

    history = client.get(f"/api/projects/{project_id}/reports/history").json()["runs"]
    assert {run["summary"]["book"] for run in history if run["kind"] == "report"} == {
        "Отчёт",
        "Строгая",
    }


def test_bundle_rejects_unknown_artifacts(project) -> None:
    client, project_id, _ = project
    url = f"/api/projects/{project_id}/reports/bundle.zip"
    assert client.get(url, params={"artifact": "0123456789abcdef"}).status_code == 404
    assert client.get(url, params={"artifact": "../../etc"}).status_code == 404
    assert client.get(url).status_code == 422
