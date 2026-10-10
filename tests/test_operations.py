"""Эксплуатация P6: готовность, метрики, JSON-журнал."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app
from sav_analytics.api_dependencies import get_settings
from sav_analytics.jobs.queue import JobQueue
from sav_analytics.logging_setup import JsonFormatter
from sav_analytics.repository import ProjectRepository


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRepository]:
    repository = ProjectRepository(tmp_path / "projects", 10_000_000)
    monkeypatch.setenv("SAV_ANALYTICS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SAV_ANALYTICS_DATABASE_URL", repository.database_url)
    get_settings.cache_clear()
    yield repository
    get_settings.cache_clear()


def test_ready_reports_database_schema_and_storage(configured: ProjectRepository) -> None:
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"]["database"] == "ok" and body["checks"]["schema"] == "ok"
    assert body["checks"]["free_disk_mb"] > 0


def test_ready_fails_without_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAV_ANALYTICS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "SAV_ANALYTICS_DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:9/none"
    )
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            response = client.get("/api/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["database"].startswith("error")
    finally:
        get_settings.cache_clear()


def test_metrics_expose_queue_and_requests(
    configured: ProjectRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = JobQueue(configured.engine)
    queue.enqueue("report.build", {}, project_id="p")
    failed = queue.enqueue("project.import", {})
    queue.claim("w", lease_seconds=30, kinds=("project.import",))
    queue.fail(failed["id"], "w", error="x", code="IMPORT_FAILED")
    with TestClient(app) as client:
        client.get("/api/health")
        text = client.get("/metrics").text
        assert 'sav_jobs{kind="report.build",status="queued"} 1' in text
        assert 'sav_job_failures_24h{kind="project.import",code="IMPORT_FAILED"} 1' in text
        assert 'sav_http_requests_total{method="GET",status="2xx"}' in text
        assert "sav_queue_oldest_seconds" in text and "sav_storage_bytes" in text

        monkeypatch.setenv("SAV_ANALYTICS_METRICS_TOKEN", "metrics-secret")
        get_settings.cache_clear()
        assert client.get("/metrics").status_code == 403
        allowed = client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})
        assert allowed.status_code == 200


def test_json_log_lines_carry_context() -> None:
    record = logging.LogRecord("sav_analytics.x", logging.INFO, "f", 1, "Job %s", ("done",), None)
    record.job_id = "j-1"
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "Job done"
    assert line["job_id"] == "j-1" and line["level"] == "INFO"
