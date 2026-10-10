"""Хранилище метаданных P3: ревизии в базе, миграции, перенос старых проектов, корзина."""

from __future__ import annotations

import json
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from sav_analytics.configuration_revision import ConfigurationConflictError
from sav_analytics.db import sqlite_url
from sav_analytics.db.engine import _create_engine, head_revision, run_migrations
from sav_analytics.db.schema import metadata
from sav_analytics.manage import main as manage
from sav_analytics.repository import ProjectNotFoundError, ProjectRepository, store
from sav_analytics.repository.legacy_import import dry_run, import_legacy
from tests.test_sav_reader import write_fixture


def _project(repository: ProjectRepository, tmp_path: Path, name: str = "Проект") -> dict:
    source = tmp_path / f"{name}.sav"
    write_fixture(source)
    with source.open("rb") as stream:
        return repository.create(name, "fixture.sav", stream)


def test_two_instances_do_not_lose_each_others_changes(tmp_path: Path) -> None:
    """Два экземпляра API с общей базой: устаревшая запись получает конфликт."""
    url = store.default_database_url(tmp_path / "projects")
    first = ProjectRepository(tmp_path / "projects", 10_000_000, url)
    second = ProjectRepository(tmp_path / "projects", 10_000_000, url)
    created = _project(first, tmp_path)
    project_id = UUID(created["id"])

    stale = second.get(project_id)
    first.rename(project_id, "Первый")
    stale["name"] = "Второй"
    with pytest.raises(ConfigurationConflictError):
        second.save_project(project_id, stale)
    assert second.get(project_id)["name"] == "Первый"
    assert [item["name"] for item in second.list()] == ["Первый"]


def test_parallel_writers_apply_every_change(tmp_path: Path) -> None:
    """Писатели с повтором при конфликте: ни одна правка не теряется."""
    url = store.default_database_url(tmp_path / "projects")
    created = _project(ProjectRepository(tmp_path / "projects", 10_000_000, url), tmp_path)
    project_id = UUID(created["id"])
    writers, rounds = 4, 5
    errors: list[BaseException] = []

    def write(index: int) -> None:
        repository = ProjectRepository(tmp_path / "projects", 10_000_000, url)
        try:
            for step in range(rounds):
                while True:
                    project = repository.get(project_id)
                    marks = project["configuration"].setdefault("label_overrides", {})
                    marks[f"w{index}-{step}"] = "x"
                    try:
                        repository.save_project(project_id, project)
                        break
                    except ConfigurationConflictError:
                        continue
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    final = ProjectRepository(tmp_path / "projects", 10_000_000, url).get(project_id)
    assert len(final["configuration"]["label_overrides"]) == writers * rounds
    assert final["configuration"]["revision"] == 1 + writers * rounds


def test_migration_chain_matches_table_definitions(tmp_path: Path) -> None:
    engine = _create_engine(sqlite_url(tmp_path / "chain.db"))
    run_migrations(engine)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert context.get_current_revision() == head_revision()
        assert compare_metadata(context, metadata) == []
    engine.dispose()


def test_trash_restore_and_purge(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", 10_000_000)
    kept = _project(repository, tmp_path, "Остаётся")
    doomed = _project(repository, tmp_path, "Удаляется")
    doomed_id = UUID(doomed["id"])

    with pytest.raises(ProjectNotFoundError):
        repository.purge(doomed_id)  # из библиотеки сразу удалить нельзя
    repository.trash(doomed_id)
    with pytest.raises(ProjectNotFoundError):
        repository.get(doomed_id)
    assert [item["id"] for item in repository.list()] == [kept["id"]]
    assert [item["id"] for item in repository.list_trash()] == [doomed["id"]]
    assert repository.restore(doomed_id)["name"] == "Удаляется"

    repository.trash(doomed_id)
    # Срок хранения не вышел — очистка ничего не трогает.
    assert repository.purge_expired(30) == []
    assert repository.purge_expired(0) == [doomed["id"]]
    assert not repository.project_dir(doomed_id).exists()
    assert repository.list_trash() == []
    assert not repository.metadata.exists(doomed["id"])
    assert repository.get(UUID(kept["id"]))["name"] == "Остаётся"


def _write_legacy(repository: ProjectRepository, project: dict, *, trashed: bool) -> None:
    """Разложить проект так, как его хранила версия до P3."""
    directory = repository.project_dir(project["id"])
    document = repository.stored_document(project["id"])
    (directory / "project.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )
    old = json.loads(json.dumps(document))
    old["configuration"]["schema_version"] = 3
    (directory / "project.v3.bak").write_text(json.dumps(old), encoding="utf-8")
    step = {"configuration": document["configuration"], "inspection": None,
            "sections": ["таблицы"]}
    (directory / "history.json").write_text(
        json.dumps({"undo": [step], "redo": []}), encoding="utf-8"
    )
    if trashed:
        trash = repository.root / ".trash"
        trash.mkdir(exist_ok=True)
        shutil.move(directory, trash / project["id"])
        (trash / project["id"] / "trashed.json").write_text(
            json.dumps({"trashed_at": "2026-09-01T10:00:00+00:00"}), encoding="utf-8"
        )


def test_legacy_projects_move_into_the_database(tmp_path: Path) -> None:
    builder = ProjectRepository(tmp_path / "projects", 10_000_000, sqlite_url(tmp_path / "a.db"))
    active = _project(builder, tmp_path, "Активный")
    binned = _project(builder, tmp_path, "В корзине")
    _write_legacy(builder, active, trashed=False)
    _write_legacy(builder, binned, trashed=True)
    (tmp_path / "projects" / "not-a-project").mkdir()

    # Проверка на копии ничего не меняет в настоящих данных.
    checked = dry_run(tmp_path / "projects", 10_000_000)
    assert sorted(checked.imported) == sorted([active["id"], binned["id"]])
    assert checked.failed == {}
    assert (tmp_path / "projects" / ".trash" / binned["id"]).is_dir()

    target = ProjectRepository(tmp_path / "projects", 10_000_000, sqlite_url(tmp_path / "b.db"))
    assert target.list() == []
    report = import_legacy(target)
    assert report.failed == {}
    assert report.trashed == [binned["id"]]
    assert [item["name"] for item in target.list()] == ["Активный"]
    trash = target.list_trash()
    assert [item["id"] for item in trash] == [binned["id"]]
    assert trash[0]["trashed_at"].startswith("2026-09-01T10:00:00")
    assert target.history(UUID(active["id"]))["undo"] == 1
    assert [item["schema_version"] for item in target.metadata.backups(active["id"])] == [3]
    assert target.restore(UUID(binned["id"]))["name"] == "В корзине"
    # Повторный запуск ничего не дублирует.
    assert sorted(import_legacy(target).skipped) == sorted([active["id"], binned["id"]])


def test_legacy_project_that_does_not_open_is_reported(tmp_path: Path) -> None:
    builder = ProjectRepository(tmp_path / "projects", 10_000_000, sqlite_url(tmp_path / "a.db"))
    broken = _project(builder, tmp_path)
    _write_legacy(builder, broken, trashed=False)
    path = builder.project_dir(broken["id"]) / "project.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["source"]["sha256"] = "не хэш"
    path.write_text(json.dumps(document), encoding="utf-8")

    target = ProjectRepository(tmp_path / "projects", 10_000_000, sqlite_url(tmp_path / "b.db"))
    report = import_legacy(target)
    assert list(report.failed) == [broken["id"]]
    assert not target.metadata.exists(broken["id"])
    assert path.is_file()


def test_manage_purge_trash_uses_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SAV_ANALYTICS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SAV_ANALYTICS_TRASH_RETENTION_DAYS", "30")
    repository = ProjectRepository(tmp_path / "projects", 10_000_000)
    monkeypatch.setenv("SAV_ANALYTICS_DATABASE_URL", repository.database_url)
    old = _project(repository, tmp_path, "Старый")
    fresh = _project(repository, tmp_path, "Свежий")
    repository.trash(UUID(fresh["id"]))
    repository.trash(UUID(old["id"]))
    repository.metadata.set_trashed(old["id"], None)
    repository.metadata.set_trashed(old["id"], datetime.now(UTC) - timedelta(days=31))

    assert manage(["purge-trash"]) == 0
    assert json.loads(capsys.readouterr().out)["purged"] == [old["id"]]
    assert [item["id"] for item in repository.list_trash()] == [fresh["id"]]
