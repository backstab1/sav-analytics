"""Импорт файла фоновым заданием (P5) и отдельный worker с процессом на задание."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.import_jobs import KIND
from sav_analytics.jobs.queue import JobQueue
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture


def _wait(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 15
    status = client.get(f"/api/imports/{job_id}").json()
    while status["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.02)
        status = client.get(f"/api/imports/{job_id}").json()
    return status


def test_import_runs_in_background_and_creates_project(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", 10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            started = client.post(
                "/api/imports",
                data={"name": "Фоновый"},
                files={"file": ("research.sav", stream, "application/octet-stream")},
            )
            assert started.status_code == 202
            assert started.json()["filename"] == "research.sav"
            done = _wait(client, started.json()["job_id"])
            assert done["status"] == "complete", done
            assert done["progress"] == 100
            project = client.get(f"/api/projects/{done['project_id']}").json()
            assert project["name"] == "Фоновый"
            assert project["inspection"]["row_count"] == 4
    finally:
        app.dependency_overrides.clear()


def test_broken_file_fails_import_with_readable_error(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", 10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            started = client.post(
                "/api/imports",
                files={"file": ("broken.sav", b"not a sav file", "application/octet-stream")},
            )
            done = _wait(client, started.json()["job_id"])
            assert done["status"] == "failed"
            assert done["error_code"] == "IMPORT_FAILED"
            assert repository.list() == []
            assert not list((tmp_path / "projects").glob(".*.uploading"))
            # Пустой файл отклоняется ещё при приёме.
            empty = client.post(
                "/api/imports", files={"file": ("empty.sav", b"", "application/octet-stream")}
            )
            assert empty.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_queued_import_can_be_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = ProjectRepository(tmp_path / "projects", 10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    # Отдельный worker: API задание только ставит, и оно ждёт в очереди.
    monkeypatch.setattr("sav_analytics.jobs.dispatch.embedded", lambda: False)
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            started = client.post(
                "/api/imports",
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()
            assert started["status"] == "queued"
            cancelled = client.post(f"/api/imports/{started['job_id']}/cancel").json()
            assert cancelled["status"] == "cancelled"
            assert not list((tmp_path / "projects").glob(".*.uploading"))
    finally:
        app.dependency_overrides.clear()


def test_standalone_worker_imports_in_separate_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sav_analytics.settings import Settings
    from sav_analytics.worker import Worker

    monkeypatch.setenv("SAV_ANALYTICS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SAV_ANALYTICS_WORKER_ISOLATION", "process")
    monkeypatch.setenv("SAV_ANALYTICS_MIN_FREE_DISK_MB", "0")
    repository = ProjectRepository(tmp_path / "projects", 10_000_000)
    monkeypatch.setenv("SAV_ANALYTICS_DATABASE_URL", repository.database_url)
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    with source.open("rb") as stream:
        staged = repository.stage_upload("research.sav", stream)
    queue = JobQueue(repository.engine)
    job = queue.enqueue(KIND, {"name": "Из worker", "staged": staged})

    worker = Worker(Settings())
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        worker.tick()
        state = queue.get(job["id"])
        if state["status"] not in {"queued", "running"} and not worker.running:
            break
        time.sleep(0.1)
    assert state["status"] == "complete", state
    assert state["worker_id"] == worker.id
    assert [item["name"] for item in repository.list()] == ["Из worker"]
