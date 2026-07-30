from io import BytesIO
from pathlib import Path
from uuid import UUID

from sav_analytics.core.report import ToplineArtifacts
from sav_analytics.report_cache import prepare_report
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture


def test_report_cache_is_reused_and_invalidated_after_configuration_change(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    project = repository.create("Report", "fixture.sav", BytesIO(source.read_bytes()))
    project_id = UUID(project["id"])
    calls = 0

    def build_artifacts(
        _path, _project, *, statistics_stream, progress_callback
    ) -> ToplineArtifacts:
        nonlocal calls
        calls += 1
        statistics_stream.write("statistics")
        return ToplineArtifacts(xlsx=b"xlsx", statistics_txt="statistics")

    monkeypatch.setattr("sav_analytics.report_cache.build_topline_artifacts", build_artifacts)

    first = prepare_report(repository, project_id, project)
    second = prepare_report(repository, project_id, project)
    assert first.cached is False
    assert second.cached is True
    assert calls == 1

    question = project["configuration"]["questions"][0]
    updated = repository.update_question(project_id, question["code"], {"label": "Changed"})
    third = prepare_report(repository, project_id, updated)
    assert third.cached is False
    assert calls == 2
