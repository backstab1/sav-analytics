import time
from pathlib import Path
from uuid import UUID, uuid4

from sav_analytics.report_cache import PreparedReport
from sav_analytics.report_jobs import get_report_job, start_report_job
from sav_analytics.repository import ProjectRepository


def test_background_report_job_exposes_progress(tmp_path: Path, monkeypatch) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=1)
    project_id = uuid4()
    project = {
        "source": {"sha256": "source"},
        "configuration": {"updated_at": "now"},
    }

    monkeypatch.setattr("sav_analytics.report_jobs.get_cached_report", lambda *_: None)

    def prepare(_repository, _project_id, _project, progress_callback):
        progress_callback(1, 2, "Расчёт")
        progress_callback(2, 2, "Запись")
        return PreparedReport(
            Path("topline.xlsx"),
            Path("statistics.txt"),
            False,
            "cache-key",
            "artifact-id",
            7,
        )

    monkeypatch.setattr("sav_analytics.report_jobs.prepare_report", prepare)
    started = start_report_job(repository, project_id, project)
    deadline = time.monotonic() + 2
    status = started
    while status["status"] != "complete" and time.monotonic() < deadline:
        time.sleep(0.01)
        status = get_report_job(UUID(started["job_id"]), project_id)

    assert status is not None
    assert status["status"] == "complete"
    assert status["progress"] == 100
    assert status["configuration_revision"] == 1
    assert status["artifact_id"] == "artifact-id"
    assert status["downloads"]["topline"].endswith("/artifact-id/topline.xlsx")


def test_background_report_job_does_not_expose_internal_error(
    tmp_path: Path, monkeypatch
) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=1)
    project_id = uuid4()
    project = {
        "source": {"sha256": "another-source"},
        "configuration": {"revision": 3, "updated_at": "now"},
    }
    monkeypatch.setattr("sav_analytics.report_jobs.get_cached_report", lambda *_: None)

    def fail(*_args, **_kwargs):
        raise RuntimeError("internal path and secret")

    monkeypatch.setattr("sav_analytics.report_jobs.prepare_report", fail)
    started = start_report_job(repository, project_id, project)
    deadline = time.monotonic() + 2
    status = started
    while status["status"] not in {"complete", "failed"} and time.monotonic() < deadline:
        time.sleep(0.01)
        status = get_report_job(UUID(started["job_id"]), project_id)

    assert status is not None
    assert status["status"] == "failed"
    assert status["error_code"] == "REPORT_BUILD_FAILED"
    assert "secret" not in status["error"]
