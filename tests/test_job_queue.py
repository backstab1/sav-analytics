"""Очередь заданий P5: идемпотентность, аренда, timeout, отмена, повтор, worker."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from sav_analytics.db.engine import utcnow
from sav_analytics.db.schema import jobs
from sav_analytics.jobs.queue import JobQueue
from sav_analytics.jobs.runtime import JobContext, JobFailure, register, run_job
from sav_analytics.repository import ProjectRepository


@pytest.fixture
def repository(tmp_path: Path) -> ProjectRepository:
    return ProjectRepository(tmp_path / "projects", 10_000_000)


@pytest.fixture
def queue(repository: ProjectRepository) -> JobQueue:
    return JobQueue(repository.engine)


calls: list[str] = []


@register("test.echo")
def _echo(context: JobContext) -> dict:
    calls.append(context.job["id"])
    for step in range(3):
        context.progress(step, 3, f"Шаг {step}")
    return {"echo": context.payload["value"]}


@register("test.flaky")
def _flaky(context: JobContext) -> dict:
    if context.job["attempts"] < 2:
        raise JobFailure("Временный сбой", "FLAKY", retryable=True)
    return {"ok": True}


@register("test.slow")
def _slow(context: JobContext) -> dict:
    for step in range(200):
        context.progress(step, 200, "Долго")
        time.sleep(0.02)
    return {}


def _age(queue: JobQueue, job_id: str, **columns: object) -> None:
    with queue.engine.begin() as connection:
        connection.execute(jobs.update().where(jobs.c.id == job_id).values(**columns))


def test_same_key_does_not_queue_twice(queue: JobQueue) -> None:
    first = queue.enqueue("test.echo", {"value": 1}, idempotency_key="k")
    second = queue.enqueue("test.echo", {"value": 2}, idempotency_key="k")
    assert second["id"] == first["id"]
    claimed = queue.claim("w1", lease_seconds=30)
    assert claimed is not None and claimed["id"] == first["id"]
    assert queue.claim("w2", lease_seconds=30) is None
    queue.complete(first["id"], "w1", {"done": True})
    # Ключ снят: та же версия ставится заново.
    third = queue.enqueue("test.echo", {"value": 3}, idempotency_key="k")
    assert third["id"] != first["id"]


def test_parallel_enqueue_with_one_key_creates_one_job(queue: JobQueue) -> None:
    ids: list[str] = []

    def enqueue() -> None:
        ids.append(queue.enqueue("test.echo", {"value": 0}, idempotency_key="same")["id"])

    threads = [threading.Thread(target=enqueue) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(ids)) == 1


def test_run_job_records_progress_and_result(
    queue: JobQueue, repository: ProjectRepository
) -> None:
    job = queue.enqueue("test.echo", {"value": "привет"})
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None
    assert run_job(queue, repository, claimed, "w", lease_seconds=30) == "complete"
    done = queue.get(job["id"])
    assert done["status"] == "complete"
    assert done["result"] == {"echo": "привет"}
    assert done["completed"] == done["total"]
    assert done["attempts"] == 1


def test_retryable_failure_is_requeued_until_attempts_run_out(
    queue: JobQueue, repository: ProjectRepository
) -> None:
    job = queue.enqueue("test.flaky", {}, max_attempts=3)
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None
    assert run_job(queue, repository, claimed, "w") == "queued"
    waiting = queue.get(job["id"])
    assert waiting["status"] == "queued" and waiting["error_code"] == "FLAKY"
    # Пауза перед повтором: сразу задание не берётся.
    assert queue.claim("w", lease_seconds=30) is None
    _age(queue, job["id"], run_after=utcnow() - timedelta(seconds=1))
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None
    assert run_job(queue, repository, claimed, "w") == "complete"


def test_dead_worker_lease_is_recovered(queue: JobQueue) -> None:
    job = queue.enqueue("test.echo", {"value": 1}, max_attempts=2)
    assert queue.claim("dead", lease_seconds=30) is not None
    assert queue.recover() == {"requeued": 0, "failed": 0, "timed_out": 0, "interrupted": 0}
    _age(queue, job["id"], lease_until=utcnow() - timedelta(seconds=1))
    assert queue.recover()["requeued"] == 1
    # Опоздавший worker уже не может записать итог.
    assert queue.complete(job["id"], "dead", {}) is False
    _age(queue, job["id"], run_after=None)
    assert queue.claim("alive", lease_seconds=30) is not None
    _age(queue, job["id"], lease_until=utcnow() - timedelta(seconds=1))
    assert queue.recover()["failed"] == 1
    assert queue.get(job["id"])["error_code"] == "WORKER_LOST"


def test_timeout_fails_job_even_with_heartbeat(queue: JobQueue) -> None:
    job = queue.enqueue("test.echo", {"value": 1}, timeout_seconds=60)
    assert queue.claim("w", lease_seconds=30) is not None
    _age(queue, job["id"], started_at=utcnow() - timedelta(seconds=61))
    assert queue.heartbeat(job["id"], "w", lease_seconds=30) == "timeout"
    assert queue.recover()["timed_out"] == 1
    failed = queue.get(job["id"])
    assert failed["status"] == "failed" and failed["error_code"] == "JOB_TIMEOUT"
    assert failed["active_key"] is None


def test_cancel_queued_and_running(queue: JobQueue, repository: ProjectRepository) -> None:
    queued = queue.enqueue("test.echo", {"value": 1}, idempotency_key="a")
    assert queue.cancel(queued["id"])["status"] == "cancelled"

    running = queue.enqueue("test.slow", {}, idempotency_key="b")
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None
    outcome: list[str] = []
    thread = threading.Thread(
        target=lambda: outcome.append(run_job(queue, repository, claimed, "w", lease_seconds=30))
    )
    thread.start()
    time.sleep(0.2)
    assert queue.cancel(running["id"])["cancel_requested"] is True
    thread.join(10)
    assert outcome == ["cancelled"]
    assert queue.get(running["id"])["status"] == "cancelled"

    # Повтор отменённого — с теми же входными данными.
    again = queue.retry(running["id"])
    assert again["status"] == "queued" and again["max_attempts"] == again["attempts"] + 1


def test_unexpected_error_text_is_not_exposed(
    queue: JobQueue, repository: ProjectRepository
) -> None:
    @register("test.broken", failure_message="Общий текст", failure_code="BROKEN")
    def _broken(_context: JobContext) -> dict:
        raise RuntimeError("секретный путь")

    job = queue.enqueue("test.broken", {})
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None
    run_job(queue, repository, claimed, "w")
    failed = queue.get(job["id"])
    assert failed["error"] == "Общий текст" and failed["error_code"] == "BROKEN"


def test_local_job_of_dead_process_is_interrupted(queue: JobQueue) -> None:
    job = queue.enqueue("test.echo", {"value": 1}, runner="local")
    # Обычный worker локальные задания не берёт.
    assert queue.claim("worker", lease_seconds=30) is None
    assert queue.claim("api:old", lease_seconds=30, runner="local") is not None
    _age(queue, job["id"], lease_until=utcnow() - timedelta(seconds=1))
    assert queue.recover(local_worker_ids=("api:new",))["interrupted"] == 1
    assert queue.get(job["id"])["error_code"] == "JOB_INTERRUPTED"


def test_cleanup_removes_old_finished_jobs(queue: JobQueue) -> None:
    old = queue.enqueue("test.echo", {"value": 1})
    queue.cancel(old["id"])
    _age(queue, old["id"], finished_at=utcnow() - timedelta(days=40))
    fresh = queue.enqueue("test.echo", {"value": 2})
    assert queue.cleanup(utcnow() - timedelta(days=30)) == 1
    with queue.engine.connect() as connection:
        remaining = [row.id for row in connection.execute(sa.select(jobs.c.id))]
    assert remaining == [fresh["id"]]


def test_standalone_worker_runs_queue_in_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repository: ProjectRepository
) -> None:
    from sav_analytics.settings import Settings
    from sav_analytics.worker import Worker

    monkeypatch.setenv("SAV_ANALYTICS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SAV_ANALYTICS_DATABASE_URL", repository.database_url)
    monkeypatch.setenv("SAV_ANALYTICS_WORKER_ISOLATION", "thread")
    monkeypatch.setenv("SAV_ANALYTICS_MIN_FREE_DISK_MB", "0")
    worker = Worker(Settings())
    first = worker.queue.enqueue("test.echo", {"value": 1})
    second = worker.queue.enqueue("test.echo", {"value": 2})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        worker.tick()
        statuses = {worker.queue.get(item["id"])["status"] for item in (first, second)}
        if statuses == {"complete"} and not worker.running:
            break
        time.sleep(0.05)
    assert statuses == {"complete"}


def test_report_build_is_one_job_per_version_with_recorded_version(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from sav_analytics.api import app, get_repository
    from tests.test_sav_reader import write_fixture

    repository = ProjectRepository(tmp_path / "projects", 10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects", files={"file": ("f.sav", stream, "application/octet-stream")}
            ).json()
            base = f"/api/projects/{project['id']}/reports"
            first = client.post(f"{base}/prepare").json()
            second = client.post(f"{base}/prepare").json()
            if second["status"] in {"queued", "running"}:
                assert second["job_id"] == first["job_id"]
            deadline = time.monotonic() + 30
            status = first
            while status["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.05)
                status = client.get(f"{base}/jobs/{first['job_id']}").json()
            assert status["status"] == "complete"
            version = client.get(f"{base}/artifacts/{status['artifact_id']}/version").json()
            assert version["source_sha256"] == project["source"]["sha256"]
            assert version["configuration_revision"] == project["configuration"]["revision"]
            assert version["environment"]["packages"]["pandas"]
            assert set(version["files"]) >= {"topline.xlsx", "statistics.txt"}
            assert version["parameters"]["revision"] == project["configuration"]["revision"]
            # Повтор той же версии — готовая сборка, без новой очереди.
            again = client.post(f"{base}/prepare").json()
            assert again["status"] == "complete" and again["cached"] is True
    finally:
        app.dependency_overrides.clear()
