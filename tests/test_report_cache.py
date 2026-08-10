import threading
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest

from sav_analytics.core.report import ToplineArtifacts
from sav_analytics.report_cache import (
    PreparedReport,
    ReportArtifactNotFoundError,
    get_report_artifact,
    prepare_report,
)
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
    assert first.artifact_id == second.artifact_id
    assert first.configuration_revision == project["configuration"]["revision"]
    assert calls == 1

    question = project["configuration"]["questions"][0]
    updated = repository.update_question(project_id, question["code"], {"label": "Changed"})
    third = prepare_report(repository, project_id, updated)
    assert third.cached is False
    assert third.artifact_id != first.artifact_id
    assert third.topline_path != first.topline_path
    assert first.topline_path.read_bytes() == b"xlsx"
    assert get_report_artifact(repository, project_id, first.artifact_id).cached is True
    assert calls == 2


def _artifact_files(repository: ProjectRepository, project_id: UUID, artifact_id: str) -> Path:
    return repository.report_cache_dir(project_id) / "artifacts" / artifact_id


def _prepared_project(
    tmp_path: Path, monkeypatch
) -> tuple[ProjectRepository, UUID, dict, list[int]]:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    project = repository.create("Report", "fixture.sav", BytesIO(source.read_bytes()))
    calls = [0]

    def build_artifacts(
        _path, _project, *, statistics_stream, progress_callback
    ) -> ToplineArtifacts:
        calls[0] += 1
        statistics_stream.write("statistics")
        return ToplineArtifacts(xlsx=b"xlsx", statistics_txt="statistics")

    monkeypatch.setattr("sav_analytics.report_cache.build_topline_artifacts", build_artifacts)
    return repository, UUID(project["id"]), project, calls


def test_cache_rebuilds_when_the_manifest_is_unreadable(tmp_path: Path, monkeypatch) -> None:
    repository, project_id, project, calls = _prepared_project(tmp_path, monkeypatch)
    first = prepare_report(repository, project_id, project)
    manifest = _artifact_files(repository, project_id, first.artifact_id) / "manifest.json"

    manifest.write_text("{ это не json", encoding="utf-8")

    assert prepare_report(repository, project_id, project).cached is False
    assert calls[0] == 2


def test_cache_rebuilds_when_the_manifest_is_missing(tmp_path: Path, monkeypatch) -> None:
    repository, project_id, project, calls = _prepared_project(tmp_path, monkeypatch)
    first = prepare_report(repository, project_id, project)

    (_artifact_files(repository, project_id, first.artifact_id) / "manifest.json").unlink()

    assert prepare_report(repository, project_id, project).cached is False
    assert calls[0] == 2


def test_cache_rebuilds_when_an_artifact_file_was_damaged(tmp_path: Path, monkeypatch) -> None:
    """Манифест хранит размер и sha256 обоих файлов — они должны проверяться.

    Без проверки побитый или обрезанный XLSX отдаётся как готовый отчёт: манифест
    цел, файлы на месте, и кэш считает комплект валидным.
    """
    repository, project_id, project, calls = _prepared_project(tmp_path, monkeypatch)
    first = prepare_report(repository, project_id, project)
    topline = _artifact_files(repository, project_id, first.artifact_id) / "topline.xlsx"

    topline.write_bytes(b"xls")  # обрезан на один байт

    rebuilt = prepare_report(repository, project_id, project)
    assert rebuilt.cached is False
    assert calls[0] == 2
    assert topline.read_bytes() == b"xlsx"


def test_cache_rebuilds_when_a_file_was_replaced_without_changing_its_size(
    tmp_path: Path, monkeypatch
) -> None:
    """Размера мало: подмена той же длины ловится только контрольной суммой."""
    repository, project_id, project, calls = _prepared_project(tmp_path, monkeypatch)
    first = prepare_report(repository, project_id, project)
    topline = _artifact_files(repository, project_id, first.artifact_id) / "topline.xlsx"

    topline.write_bytes(b"XLSX")

    assert prepare_report(repository, project_id, project).cached is False
    assert calls[0] == 2


def test_damaged_artifact_is_not_served_by_download(tmp_path: Path, monkeypatch) -> None:
    repository, project_id, project, _ = _prepared_project(tmp_path, monkeypatch)
    first = prepare_report(repository, project_id, project)
    statistics = _artifact_files(repository, project_id, first.artifact_id) / "statistics.txt"

    statistics.write_text("подменено", encoding="utf-8")

    with pytest.raises(ReportArtifactNotFoundError):
        get_report_artifact(repository, project_id, first.artifact_id)


def test_two_simultaneous_prepares_build_the_report_once(tmp_path: Path, monkeypatch) -> None:
    """Одинаковая подготовка из двух потоков не должна считаться дважды.

    Сборка идёт под ключом кэша: второй поток обязан дождаться первого и
    получить готовый артефакт, а не начать свой расчёт поверх тех же файлов.
    """
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    project = repository.create("Report", "fixture.sav", BytesIO(source.read_bytes()))
    project_id = UUID(project["id"])

    entered = threading.Event()
    release = threading.Event()
    calls = []

    def build_artifacts(
        _path, _project, *, statistics_stream, progress_callback
    ) -> ToplineArtifacts:
        calls.append(1)
        entered.set()
        # Держим первый расчёт открытым, пока второй поток стучится в тот же ключ.
        release.wait(timeout=10)
        statistics_stream.write("statistics")
        return ToplineArtifacts(xlsx=b"xlsx", statistics_txt="statistics")

    monkeypatch.setattr("sav_analytics.report_cache.build_topline_artifacts", build_artifacts)

    results: dict[str, PreparedReport] = {}

    def run(name: str) -> None:
        results[name] = prepare_report(repository, project_id, project)

    first = threading.Thread(target=run, args=("first",))
    first.start()
    assert entered.wait(timeout=10)

    second = threading.Thread(target=run, args=("second",))
    second.start()
    # Второй поток обязан стоять на блокировке, пока первый не отпустит.
    second.join(timeout=0.5)
    assert second.is_alive()

    release.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert len(calls) == 1
    assert results["first"].cached is False
    assert results["second"].cached is True
    assert results["first"].artifact_id == results["second"].artifact_id


def test_a_failed_build_leaves_no_half_written_artifact(tmp_path: Path, monkeypatch) -> None:
    """Падение расчёта не должно оставлять комплект, который кэш примет за готовый."""
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    project = repository.create("Report", "fixture.sav", BytesIO(source.read_bytes()))
    project_id = UUID(project["id"])
    attempts = []

    def failing(_path, _project, *, statistics_stream, progress_callback) -> ToplineArtifacts:
        attempts.append(1)
        statistics_stream.write("частично записано")
        raise RuntimeError("расчёт упал")

    monkeypatch.setattr("sav_analytics.report_cache.build_topline_artifacts", failing)
    with pytest.raises(RuntimeError):
        prepare_report(repository, project_id, project)

    def succeeding(_path, _project, *, statistics_stream, progress_callback) -> ToplineArtifacts:
        attempts.append(1)
        statistics_stream.write("statistics")
        return ToplineArtifacts(xlsx=b"xlsx", statistics_txt="statistics")

    monkeypatch.setattr("sav_analytics.report_cache.build_topline_artifacts", succeeding)
    recovered = prepare_report(repository, project_id, project)

    assert len(attempts) == 2
    assert recovered.cached is False
    assert recovered.topline_path.read_bytes() == b"xlsx"
