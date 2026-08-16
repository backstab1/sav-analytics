import json
import time
from pathlib import Path
from uuid import UUID

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.repository import STRUCTURE_VERSION, ProjectRepository
from tests.test_sav_reader import write_fixture, write_grouped_fixture


def _prepare_report(client: TestClient, project_id: str) -> dict:
    result = client.post(f"/api/projects/{project_id}/reports/prepare").json()
    deadline = time.monotonic() + 10
    while result["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.01)
        result = client.get(
            f"/api/projects/{project_id}/reports/jobs/{result['job_id']}"
        ).json()
    assert result["status"] == "complete"
    return result


def test_create_project_keeps_source_and_returns_inspection(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            response = client.post(
                "/api/projects",
                data={"name": "Тестовый проект"},
                files={"file": ("research.sav", stream, "application/octet-stream")},
            )
            assert response.status_code == 201
            project = response.json()
            assert project["name"] == "Тестовый проект"
            assert project["inspection"]["row_count"] == 4
            assert project["configuration"]["schema_version"] == 2
            assert project["configuration"]["report_settings"] == {
                "compare_to_total": False,
                "compare_target": "rest",
                "compare_pairwise": False,
                "confidence_level": 0.95,
                "bonferroni": False,
                "show_p_values": False,
                "minimum_base": 30,
                "weight_variable": None,
                "calculated_weight_id": None,
                "wave_comparison": "none",
                "wave_control_value": None,
            }
            not_prepared = client.get(
                f"/api/projects/{project['id']}/reports/topline.xlsx"
            )
            assert not_prepared.status_code == 409
            prepared = _prepare_report(client, project["id"])
            assert prepared["configuration_revision"] == 1
            assert prepared["artifact_id"] == prepared["cache_key"]
            report = client.get(prepared["downloads"]["topline"])
            assert report.status_code == 200
            assert report.content.startswith(b"PK")
            assert report.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            statistics = client.get(prepared["downloads"]["statistics"])
            assert statistics.status_code == 200
            assert statistics.headers["content-type"].startswith("text/plain")
            prepared_again = client.post(
                f"/api/projects/{project['id']}/reports/prepare"
            )
            assert prepared_again.status_code == 200
            assert prepared_again.json()["cached"] is True
            assert prepared_again.json()["status"] == "complete"
            job = client.get(
                f"/api/projects/{project['id']}/reports/jobs/"
                f"{prepared_again.json()['job_id']}"
            )
            assert job.status_code == 200
            assert job.json()["progress"] == 100
            assert "СТАТИСТИЧЕСКИЙ АУДИТ ТОПЛАЙНА" in statistics.text
            changed = client.patch(
                f"/api/projects/{project['id']}/questions/Q1",
                headers={"If-Match": "1"},
                json={"label": "Изменённый вопрос"},
            )
            assert changed.status_code == 200
            assert client.get(
                f"/api/projects/{project['id']}/reports/topline.xlsx"
            ).status_code == 409
            assert client.get(prepared["downloads"]["topline"]).status_code == 200
            technical_preview = client.get(
                f"/api/projects/{project['id']}/questions/id/preview"
            )
            assert technical_preview.status_code == 422
            assert "предпросмотр" in technical_preview.json()["detail"].lower()
            assert repository.source_path(project["id"]).read_bytes() == source.read_bytes()
    finally:
        app.dependency_overrides.clear()


def test_rejects_non_sav_extension(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=1024)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/projects",
                headers={"X-Request-ID": "upload-test-1"},
                files={"file": ("data.csv", b"a,b\n1,2", "text/csv")},
            )
        assert response.status_code == 422
        assert response.json()["error_code"] == "UNPROCESSABLE_ENTITY"
        assert response.json()["request_id"] == "upload-test-1"
        assert response.headers["X-Request-ID"] == "upload-test-1"
    finally:
        app.dependency_overrides.clear()


def test_rejects_corrupted_sav_with_json_error(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=1024)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/projects",
                files={"file": ("broken.sav", b"not an spss file", "application/octet-stream")},
            )
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error_code"] == "UNPROCESSABLE_ENTITY"
        assert response.json()["request_id"]
        assert "SPSS SAV" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_question_configuration_and_preview_are_persisted(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                data={"name": "Настройка"},
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()
            project_id = project["id"]

            updated = client.patch(
                f"/api/projects/{project_id}/questions/Q1",
                json={"label": "Пол респондента", "included_in_report": False},
            )
            assert updated.status_code == 200
            q1 = next(
                item
                for item in updated.json()["configuration"]["questions"]
                if item["code"] == "Q1"
            )
            assert q1["label"] == "Пол респондента"
            assert not q1["included_in_report"]

            preview = client.get(f"/api/projects/{project_id}/questions/Q1/preview")
            assert preview.status_code == 200
            assert preview.json()["total_base"] == 4
            assert preview.json()["valid_base"] == 4
            assert [row["count"] for row in preview.json()["rows"]] == [2, 2]

            nps = client.patch(
                f"/api/projects/{project_id}/questions/Q2",
                json={"special_metric": "nps"},
            )
            assert nps.status_code == 200
            q2 = next(
                item
                for item in nps.json()["configuration"]["questions"]
                if item["code"] == "Q2"
            )
            assert q2["special_metric"] == "nps"
            invalid_csat = client.patch(
                f"/api/projects/{project_id}/questions/Q2",
                json={"special_metric": "csat"},
            )
            assert invalid_csat.status_code == 422
            assert "1–5" in invalid_csat.json()["detail"]

            codes = [item["code"] for item in project["configuration"]["questions"]]
            reordered = client.put(
                f"/api/projects/{project_id}/questions/order",
                json={"codes": list(reversed(codes))},
            )
            assert reordered.status_code == 200
            assert reordered.json()["configuration"]["questions"][0]["code"] == codes[-1]

            persisted = client.get(f"/api/projects/{project_id}").json()
            assert persisted["configuration"]["questions"][0]["code"] == codes[-1]
    finally:
        app.dependency_overrides.clear()


def test_request_validation_and_revision_errors_have_stable_codes(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=1024)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            validation = client.post("/api/projects")
            invalid_revision = client.post(
                "/api/projects",
                headers={"If-Match": "not-a-revision"},
            )
        assert validation.status_code == 422
        assert validation.json()["error_code"] == "REQUEST_VALIDATION_FAILED"
        assert validation.json()["request_id"]
        assert invalid_revision.status_code == 400
        assert invalid_revision.json()["error_code"] == "INVALID_CONFIGURATION_REVISION"
        assert invalid_revision.json()["request_id"]
    finally:
        app.dependency_overrides.clear()


def test_question_update_rejects_unsupported_report_types(tmp_path: Path) -> None:
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
            for question_type in ("multiple_choice_categorical", "ranking"):
                response = client.patch(
                    f"/api/projects/{project_id}/questions/Q1",
                    json={"question_type": question_type},
                )
                assert response.status_code == 422
                assert "не поддерживается" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_configuration_revision_rejects_stale_update(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()
            project_id = project["id"]
            initial_revision = project["configuration"]["revision"]

            updated = client.patch(
                f"/api/projects/{project_id}/questions/Q1",
                headers={"If-Match": str(initial_revision)},
                json={"label": "Первая правка"},
            )
            stale = client.patch(
                f"/api/projects/{project_id}/questions/Q1",
                headers={"If-Match": str(initial_revision)},
                json={"label": "Устаревшая правка"},
            )

            assert updated.status_code == 200
            assert updated.json()["configuration"]["revision"] == initial_revision + 1
            assert stale.status_code == 409
            assert stale.json()["error_code"] == "CONFIGURATION_CONFLICT"
            assert stale.json()["request_id"]
            assert "другой вкладке" in stale.json()["detail"]
            persisted = client.get(f"/api/projects/{project_id}").json()
            question = next(
                item
                for item in persisted["configuration"]["questions"]
                if item["code"] == "Q1"
            )
            assert question["label"] == "Первая правка"
            assert persisted["configuration"]["revision"] == initial_revision + 1
    finally:
        app.dependency_overrides.clear()


def test_unexpected_api_error_is_safe_and_traceable(tmp_path: Path, monkeypatch) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=1024)
    app.dependency_overrides[get_repository] = lambda: repository

    def fail(_project_id):
        raise RuntimeError("secret filesystem path")

    monkeypatch.setattr(repository, "get", fail)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(
                "/api/projects/00000000-0000-0000-0000-000000000001",
                headers={"X-Request-ID": "diagnostic-42"},
            )
        assert response.status_code == 500
        assert response.json()["error_code"] == "INTERNAL_ERROR"
        assert response.json()["request_id"] == "diagnostic-42"
        assert response.headers["X-Request-ID"] == "diagnostic-42"
        assert "secret" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_nps_accepts_labelled_spss_user_missing_outside_scale(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "nps_missing.sav"
    frame = pd.DataFrame({"NPS": [0, 7, 10, 99]})
    pyreadstat.write_sav(
        frame,
        source,
        variable_value_labels={
            "NPS": {0: "0", 7: "7", 10: "10", 99: "Затрудняюсь ответить"}
        },
        missing_ranges={"NPS": [99]},
        variable_measure={"NPS": "scale"},
    )
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects",
                files={"file": ("nps.sav", stream, "application/octet-stream")},
            ).json()["id"]

            response = client.patch(
                f"/api/projects/{project_id}/questions/NPS",
                json={"special_metric": "nps"},
            )

            assert response.status_code == 200
            question = next(
                item
                for item in response.json()["configuration"]["questions"]
                if item["code"] == "NPS"
            )
            assert question["special_metric"] == "nps"
            assert question["valid_count"] == 3
            assert question["missing_count"] == 1
    finally:
        app.dependency_overrides.clear()


def test_numeric_recoding_crud_and_preview(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    recoding = {
        "code": "SCORE_GROUP",
        "name": "Группы оценки",
        "source_variable": "Q2",
        "categories": [
            {"label": "Низкая", "lower": 0, "upper": 7},
            {"label": "Высокая", "lower": 8, "upper": 10},
        ],
    }
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects",
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()["id"]

            created = client.post(
                f"/api/projects/{project_id}/recodings", json=recoding
            )
            assert created.status_code == 201
            saved = created.json()["configuration"]["recodings"][0]
            recoding_id = saved["id"]

            preview = client.get(
                f"/api/projects/{project_id}/recodings/{recoding_id}/preview"
            )
            assert preview.status_code == 200
            assert [row["count"] for row in preview.json()["rows"]] == [1, 2]

            overlapping = {**recoding, "code": "OVERLAP"}
            overlapping["categories"] = [
                {"label": "A", "lower": 0, "upper": 7},
                {"label": "B", "lower": 7, "upper": 10},
            ]
            rejected = client.post(
                f"/api/projects/{project_id}/recodings", json=overlapping
            )
            assert rejected.status_code == 422
            assert "пересекаются" in rejected.json()["detail"]

            deleted = client.delete(
                f"/api/projects/{project_id}/recodings/{recoding_id}"
            )
            assert deleted.status_code == 200
            assert deleted.json()["configuration"]["recodings"] == []

            categorical = client.post(
                f"/api/projects/{project_id}/recodings",
                json={
                    "mode": "categories",
                    "code": "GENDER_GROUP",
                    "name": "Группы пола",
                    "source_variable": "Q1",
                    "categories": [
                        {"label": "Мужчины", "values": [1]},
                        {"label": "Женщины", "values": [2]},
                    ],
                },
            )
            assert categorical.status_code == 201
            categorical_id = categorical.json()["configuration"]["recodings"][0]["id"]
            category_preview = client.get(
                f"/api/projects/{project_id}/recodings/{categorical_id}/preview"
            )
            assert category_preview.status_code == 200
            assert category_preview.json()["mode"] == "categories"
            assert [row["count"] for row in category_preview.json()["rows"]] == [2, 2]
    finally:
        app.dependency_overrides.clear()


def test_refresh_structure_preserves_group_settings(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "grouped.sav"
    write_grouped_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects",
                files={"file": ("grouped.sav", stream, "application/octet-stream")},
            ).json()["id"]
            client.patch(
                f"/api/projects/{project_id}/questions/Q5",
                json={"label": "Моя матрица", "included_in_report": False},
            )

            refreshed = client.post(
                f"/api/projects/{project_id}/structure/refresh"
            )

            assert refreshed.status_code == 200
            matrix = next(
                item
                for item in refreshed.json()["configuration"]["questions"]
                if item["code"] == "Q5"
            )
            assert matrix["label"] == "Моя матрица"
            assert not matrix["included_in_report"]
            assert matrix["special_values"] == [99.0]
    finally:
        app.dependency_overrides.clear()


def test_legacy_project_structure_is_refreshed_on_open(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    source = tmp_path / "grouped.sav"
    write_grouped_fixture(source)
    with source.open("rb") as stream:
        created = repository.create("Legacy", "grouped.sav", stream)

    project_id = UUID(created["id"])
    metadata_path = tmp_path / "projects" / created["id"] / "project.json"
    legacy = json.loads(metadata_path.read_text(encoding="utf-8"))
    legacy["configuration"].pop("structure_version")
    legacy["configuration"].pop("report_settings")
    legacy["configuration"]["questions"] = [
        {
            "code": variable["name"],
            "label": variable["label"],
            "question_type": "single_choice",
            "role": "question",
            "source_variables": [variable["name"]],
            "included_in_report": variable["name"] != "Q5_1",
            "valid_count": variable["valid_count"],
            "missing_count": variable["missing_count"],
            "unique_count": variable["unique_count"],
            "value_labels": variable["value_labels"],
        }
        for variable in legacy["inspection"]["variables"]
    ]
    metadata_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    migrated = repository.get(project_id)

    assert migrated["configuration"]["schema_version"] == 2
    assert migrated["configuration"]["report_settings"]["confidence_level"] == 0.95
    matrix = next(
        question
        for question in migrated["configuration"]["questions"]
        if question["code"] == "Q5"
    )
    assert matrix["source_variables"] == ["Q5_1", "Q5_2"]
    assert not matrix["included_in_report"]
    assert migrated["configuration"]["structure_version"] == STRUCTURE_VERSION


def test_legacy_banner_settings_are_exposed_as_report_settings(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    with source.open("rb") as stream:
        created = repository.create("Legacy banner", "fixture.sav", stream)

    banner_id = "00000000-0000-0000-0000-000000000001"
    metadata_path = tmp_path / "projects" / created["id"] / "project.json"
    legacy = json.loads(metadata_path.read_text(encoding="utf-8"))
    legacy["configuration"].pop("report_settings")
    legacy["configuration"]["report_banner_id"] = banner_id
    legacy["configuration"]["banners"] = [
        {
            "id": banner_id,
            "name": "Пол",
            "blocks": [
                {
                    "label": "Пол",
                    "sources": [{"kind": "question", "ref": "Q1"}],
                }
            ],
            "compare_to_total": True,
            "compare_target": "total",
            "compare_pairwise": True,
            "confidence_level": 0.9,
            "bonferroni": True,
            "minimum_base": 25,
        }
    ]
    metadata_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    migrated = repository.get(UUID(created["id"]))

    settings = migrated["configuration"]["report_settings"]
    assert settings["confidence_level"] == 0.9
    assert settings["minimum_base"] == 25
    assert settings["compare_to_total"] is True
    assert settings["compare_target"] == "total"
    assert settings["compare_pairwise"] is True
    assert settings["bonferroni"] is True


def test_preflight_blocks_prepare_and_reports_the_reason(tmp_path: Path) -> None:
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

            clean = client.get(f"/api/projects/{project_id}/reports/preflight")
            assert clean.status_code == 200
            assert clean.json()["can_prepare"] is True
            assert clean.json()["errors"] == []
            assert _prepare_report(client, project_id)["status"] == "complete"

            empty_filter = client.post(
                f"/api/projects/{project_id}/filters",
                json={
                    "name": "Пустой",
                    "rule": {
                        "operator": "and",
                        "items": [
                            {
                                "source": {"kind": "question", "ref": "Q1"},
                                "operator": "eq",
                                "values": [999],
                            }
                        ],
                    },
                },
            ).json()["configuration"]["filters"][0]
            assigned = client.put(
                f"/api/projects/{project_id}/report-filter",
                json={"filter_id": empty_filter["id"]},
            )
            assert assigned.status_code == 200

            checked = client.get(f"/api/projects/{project_id}/reports/preflight")
            assert checked.status_code == 200
            assert checked.json()["can_prepare"] is False
            assert checked.json()["errors"][0]["code"] == "REPORT_NOT_BUILDABLE"

            # Сборка не должна запускаться и падать внутри задачи: отказ приходит
            # сразу и с понятной причиной.
            blocked = client.post(f"/api/projects/{project_id}/reports/prepare")
            assert blocked.status_code == 422
            assert blocked.json()["error_code"] == "REPORT_PREFLIGHT_FAILED"
            assert "пустую выборку" in blocked.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_not_applicable_marks_a_whole_group_in_one_revision(tmp_path: Path) -> None:
    """Заглушка лежит сразу в нескольких вопросах, поэтому пометка — одна запись."""
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "skip.sav"
    frame = pd.DataFrame(
        {
            "ST_TRUD": [11] * 20 + [41] * 40 + [0] * 40,
            "TIP_DG": [1] * 20 + [2] * 40 + [0] * 40,
        }
    )
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"ST_TRUD": "Статус", "TIP_DG": "Оформление"},
        variable_value_labels={
            "ST_TRUD": {11: "Работодатели", 41: "Наемные"},
            "TIP_DG": {1: "Договор", 2: "Устно"},
        },
        variable_measure={"ST_TRUD": "nominal", "TIP_DG": "nominal"},
    )
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                files={"file": ("skip.sav", stream, "application/octet-stream")},
            ).json()
            project_id = project["id"]

            suggestions = client.get(
                f"/api/projects/{project_id}/questions/not-applicable-suggestions"
            )
            assert suggestions.status_code == 200
            groups = suggestions.json()["groups"]
            assert len(groups) == 1
            assert groups[0]["respondents"] == 40
            assert {item["question_code"] for item in groups[0]["candidates"]} == {
                "ST_TRUD",
                "TIP_DG",
            }

            before = project["configuration"]["revision"]
            applied = client.post(
                f"/api/projects/{project_id}/questions/not-applicable",
                headers={"If-Match": str(before)},
                json={
                    "marks": [
                        {"code": item["question_code"], "values": [item["value"]]}
                        for item in groups[0]["candidates"]
                    ]
                },
            )
            assert applied.status_code == 200
            configuration = applied.json()["configuration"]
            # Оба вопроса помечены, но ревизия выросла ровно на один шаг.
            assert configuration["revision"] == before + 1
            marked = {
                item["code"]: item.get("not_applicable_values")
                for item in configuration["questions"]
            }
            assert marked == {"ST_TRUD": [0], "TIP_DG": [0]}

            confirmed = client.get(
                f"/api/projects/{project_id}/questions/not-applicable-suggestions"
            ).json()["groups"][0]
            assert all(item["already_marked"] for item in confirmed["candidates"])
    finally:
        app.dependency_overrides.clear()


def test_schema_1_settings_move_off_the_banner_and_leave_a_backup(
    tmp_path: Path,
) -> None:
    """Миграция берёт значения активного баннера и оставляет копию исходного файла."""
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    with source.open("rb") as stream:
        created = repository.create("Схема 1", "fixture.sav", stream)

    project_id = UUID(created["id"])
    metadata_path = tmp_path / "projects" / created["id"] / "project.json"
    legacy = json.loads(metadata_path.read_text(encoding="utf-8"))
    legacy["configuration"]["schema_version"] = 1
    legacy["configuration"].pop("report_settings")
    legacy["configuration"]["banners"] = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Неактивный",
            "confidence_level": 0.99,
            "minimum_base": 100,
            "blocks": [{"sources": [{"kind": "question", "ref": "Q1"}]}],
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "Активный",
            "confidence_level": 0.9,
            "minimum_base": 50,
            "bonferroni": True,
            "compare_target": "total",
            "blocks": [{"sources": [{"kind": "question", "ref": "Q1"}]}],
        },
    ]
    legacy["configuration"]["report_banner_id"] = "22222222-2222-2222-2222-222222222222"
    metadata_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    migrated = repository.get(project_id)
    configuration = migrated["configuration"]

    assert configuration["schema_version"] == 2
    # Источник — активный баннер: именно его значения применял расчёт до схемы 2.
    assert configuration["report_settings"]["confidence_level"] == 0.9
    assert configuration["report_settings"]["minimum_base"] == 50
    assert configuration["report_settings"]["bonferroni"] is True
    assert configuration["report_settings"]["compare_target"] == "total"
    for banner in configuration["banners"]:
        assert set(banner) == {"id", "name", "blocks"}

    backup = metadata_path.with_suffix(".v1.bak")
    assert backup.is_file()
    saved = json.loads(backup.read_text(encoding="utf-8"))
    assert saved["configuration"]["schema_version"] == 1
    assert saved["configuration"]["banners"][1]["confidence_level"] == 0.9

    # Файл на диске переписан, повторное открытие ничего не меняет и копию не трогает.
    stored = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert stored["configuration"]["schema_version"] == 2
    revision = stored["configuration"]["revision"]
    assert repository.get(project_id)["configuration"]["revision"] == revision


def test_banner_crud_and_nested_preview(tmp_path: Path) -> None:
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
            recoding = client.post(
                f"/api/projects/{project_id}/recodings",
                json={
                    "mode": "ranges",
                    "code": "SCORE_GROUP",
                    "name": "Группы оценки",
                    "source_variable": "Q2",
                    "categories": [
                        {"label": "Низкая", "lower": 0, "upper": 7},
                        {"label": "Высокая", "lower": 8, "upper": 10},
                    ],
                },
            ).json()["configuration"]["recodings"][0]
            banner = client.post(
                f"/api/projects/{project_id}/banners",
                json={
                    "name": "Пол и оценка",
                    "blocks": [
                        {
                            "label": "Пол → Оценка",
                            "sources": [
                                {"kind": "question", "ref": "Q1"},
                                {"kind": "recoding", "ref": recoding["id"]},
                            ],
                        }
                    ],
                },
            )
            assert banner.status_code == 201
            banner_id = banner.json()["configuration"]["banners"][0]["id"]

            preview = client.get(
                f"/api/projects/{project_id}/banners/{banner_id}/preview"
            )
            assert preview.status_code == 200
            assert preview.json()["columns"][0]["label"] == "Total"
            assert len(preview.json()["columns"]) == 5

            second = client.post(
                f"/api/projects/{project_id}/banners",
                json={
                    "name": "Пол",
                    "compare_to_total": True,
                    "compare_pairwise": True,
                    "blocks": [
                        {
                            "label": "Пол",
                            "sources": [{"kind": "question", "ref": "Q1"}],
                        }
                    ],
                },
            )
            assert second.status_code == 201
            second_configuration = second.json()["configuration"]
            second_id = second_configuration["banners"][1]["id"]
            assert second_configuration["report_banner_id"] == second_id
            # Старый клиент прислал настройки на баннере: они попадают в настройки
            # отчёта, а в самом баннере не сохраняются — у значения одно место.
            assert second_configuration["report_settings"]["compare_to_total"] is True
            assert second_configuration["report_settings"]["compare_pairwise"] is True
            saved_banner = second_configuration["banners"][1]
            assert set(saved_banner) == {"id", "name", "blocks"}
            assert "compare_to_total" not in saved_banner["blocks"][0]

            selected = client.put(
                f"/api/projects/{project_id}/report-banner",
                json={"banner_id": banner_id},
            )
            assert selected.status_code == 200
            assert selected.json()["configuration"]["report_banner_id"] == banner_id

            deleted = client.delete(
                f"/api/projects/{project_id}/banners/{banner_id}"
            )
            assert deleted.status_code == 200
            assert len(deleted.json()["configuration"]["banners"]) == 1
            assert deleted.json()["configuration"]["report_banner_id"] == second_id
    finally:
        app.dependency_overrides.clear()


def test_report_settings_are_saved_separately_from_banner(tmp_path: Path) -> None:
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
            created = client.post(
                f"/api/projects/{project_id}/banners",
                json={
                    "name": "Пол",
                    "blocks": [
                        {
                            "label": "Пол",
                            "sources": [{"kind": "question", "ref": "Q1"}],
                        }
                    ],
                },
            )
            assert created.status_code == 201
            banner = created.json()["configuration"]["banners"][0]
            assert set(banner) == {"id", "name", "blocks"}

            invalid_wave = client.put(
                f"/api/projects/{project_id}/report-settings",
                json={"wave_comparison": "previous"},
            )
            assert invalid_wave.status_code == 422
            assert "роли «Волна»" in invalid_wave.json()["detail"]

            updated = client.put(
                f"/api/projects/{project_id}/report-settings",
                json={
                    "compare_to_total": True,
                    "compare_target": "rest",
                    "compare_pairwise": True,
                    "confidence_level": 0.9,
                    "bonferroni": True,
                    "show_p_values": True,
                    "minimum_base": 25,
                    "weight_variable": None,
                    "calculated_weight_id": None,
                    "wave_comparison": "none",
                    "wave_control_value": None,
                },
            )
            assert updated.status_code == 200
            configuration = updated.json()["configuration"]
            assert configuration["report_settings"]["confidence_level"] == 0.9
            assert configuration["report_settings"]["minimum_base"] == 25
            assert configuration["report_settings"]["compare_to_total"] is True
            assert configuration["report_settings"]["compare_pairwise"] is True
            assert configuration["report_settings"]["show_p_values"] is True
            assert set(configuration["banners"][0]) == {"id", "name", "blocks"}
    finally:
        app.dependency_overrides.clear()


def test_recoding_delete_is_blocked_by_banner_and_filter_dependencies(
    tmp_path: Path,
) -> None:
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
            recoding = client.post(
                f"/api/projects/{project_id}/recodings",
                json={
                    "mode": "ranges",
                    "code": "SCORE_GROUP",
                    "name": "Группы оценки",
                    "source_variable": "Q2",
                    "categories": [
                        {"label": "Низкая", "lower": 0, "upper": 7},
                        {"label": "Высокая", "lower": 8, "upper": 10},
                    ],
                },
            ).json()["configuration"]["recodings"][0]
            saved_filter = client.post(
                f"/api/projects/{project_id}/filters",
                json={
                    "name": "Высокая оценка",
                    "rule": {
                        "operator": "and",
                        "items": [
                            {
                                "source": {"kind": "recoding", "ref": recoding["id"]},
                                "operator": "eq",
                                "values": ["Высокая"],
                            }
                        ],
                    },
                },
            ).json()["configuration"]["filters"][0]
            saved_banner = client.post(
                f"/api/projects/{project_id}/banners",
                json={
                    "name": "Оценка",
                    "blocks": [
                        {
                            "sources": [
                                {"kind": "recoding", "ref": recoding["id"]}
                            ]
                        }
                    ],
                },
            ).json()["configuration"]["banners"][0]

            blocked = client.delete(
                f"/api/projects/{project_id}/recodings/{recoding['id']}"
            )

            assert blocked.status_code == 422
            assert "баннер «Оценка»" in blocked.json()["detail"]
            assert "фильтр «Высокая оценка»" in blocked.json()["detail"]

            assert client.delete(
                f"/api/projects/{project_id}/filters/{saved_filter['id']}"
            ).status_code == 200
            assert client.delete(
                f"/api/projects/{project_id}/banners/{saved_banner['id']}"
            ).status_code == 200
            deleted = client.delete(
                f"/api/projects/{project_id}/recodings/{recoding['id']}"
            )
            assert deleted.status_code == 200
            assert deleted.json()["configuration"]["recodings"] == []
    finally:
        app.dependency_overrides.clear()


def test_filter_crud_preview_and_question_base(tmp_path: Path) -> None:
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
            created = client.post(
                f"/api/projects/{project_id}/filters",
                json={
                    "name": "Только мужчины",
                    "rule": {
                        "operator": "and",
                        "items": [
                            {
                                "source": {"kind": "question", "ref": "Q1"},
                                "operator": "eq",
                                "values": [1],
                            }
                        ],
                    },
                },
            )
            assert created.status_code == 201
            filter_id = created.json()["configuration"]["filters"][0]["id"]

            draft_preview = client.post(
                f"/api/projects/{project_id}/filters/preview",
                json={
                    "name": "Черновик",
                    "rule": {
                        "operator": "or",
                        "items": [
                            {
                                "source": {"kind": "question", "ref": "Q1"},
                                "operator": "eq",
                                "values": [1],
                            },
                            {
                                "kind": "group",
                                "operator": "and",
                                "items": [
                                    {
                                        "source": {"kind": "question", "ref": "Q2"},
                                        "operator": "gt",
                                        "lower": 8,
                                    }
                                ],
                            },
                        ],
                    },
                },
            )
            assert draft_preview.status_code == 200
            assert draft_preview.json()["selected"] == 3
            assert len(draft_preview.json()["steps"]) == 3

            preview = client.get(
                f"/api/projects/{project_id}/filters/{filter_id}/preview"
            )
            assert preview.status_code == 200
            assert preview.json()["selected"] == 2

            assigned = client.put(
                f"/api/projects/{project_id}/questions/Q2/base",
                json={"filter_id": filter_id},
            )
            assert assigned.status_code == 200
            q2 = next(
                item
                for item in assigned.json()["configuration"]["questions"]
                if item["code"] == "Q2"
            )
            assert q2["base_filter_id"] == filter_id

            report_filter = client.put(
                f"/api/projects/{project_id}/report-filter",
                json={"filter_id": filter_id},
            )
            assert report_filter.status_code == 200
            assert report_filter.json()["configuration"]["report_filter_id"] == filter_id

            blocked = client.delete(
                f"/api/projects/{project_id}/filters/{filter_id}"
            )
            assert blocked.status_code == 422

            client.put(
                f"/api/projects/{project_id}/questions/Q2/base",
                json={"filter_id": None},
            )
            still_blocked = client.delete(
                f"/api/projects/{project_id}/filters/{filter_id}"
            )
            assert still_blocked.status_code == 422

            client.put(
                f"/api/projects/{project_id}/report-filter",
                json={"filter_id": None},
            )
            deleted = client.delete(
                f"/api/projects/{project_id}/filters/{filter_id}"
            )
            assert deleted.status_code == 200
            assert deleted.json()["configuration"]["filters"] == []
    finally:
        app.dependency_overrides.clear()


def test_calculated_weight_crud_preview_and_report_usage(tmp_path: Path) -> None:
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
            created = client.post(
                f"/api/projects/{project_id}/weights",
                json={
                    "name": "Вес по полу",
                    "dimensions": [
                        {
                            "variable": "Q1",
                            "label": "Пол",
                            "targets": [
                                {"label": "Мужчина", "values": [1], "percent": 50},
                                {"label": "Женщина", "values": [2], "percent": 50},
                            ],
                        }
                    ],
                    "lower_bound": 0.3,
                    "upper_bound": 3.0,
                },
            )
            assert created.status_code == 201
            weight = created.json()["configuration"]["calculated_weights"][0]

            preview = client.get(
                f"/api/projects/{project_id}/weights/{weight['id']}/preview"
            )
            assert preview.status_code == 200
            assert preview.json()["mean"] == pytest.approx(1)
            assert preview.json()["distributions"][0]["categories"][0][
                "after_percent"
            ] == pytest.approx(50)
            exported = client.get(
                f"/api/projects/{project_id}/weights/{weight['id']}/export.xlsx"
            )
            assert exported.status_code == 200
            assert exported.content.startswith(b"PK")
            assert exported.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            banner = client.post(
                f"/api/projects/{project_id}/banners",
                json={
                    "name": "Основной",
                    "blocks": [
                        {
                            "label": "Пол",
                            "sources": [{"kind": "question", "ref": "Q1"}],
                        }
                    ],
                },
            )
            assert banner.status_code == 201
            report_settings = client.put(
                f"/api/projects/{project_id}/report-settings",
                json={"calculated_weight_id": weight["id"]},
            )
            assert report_settings.status_code == 200
            prepared = _prepare_report(client, project_id)
            report = client.get(prepared["downloads"]["topline"])
            assert report.status_code == 200
            blocked = client.delete(
                f"/api/projects/{project_id}/weights/{weight['id']}"
            )
            assert blocked.status_code == 422
            assert "настройка отчёта" in blocked.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_saving_a_question_clears_the_review_status(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "grouped.sav"
    write_grouped_fixture(source)

    def question(payload: dict, code: str) -> dict:
        return next(
            item for item in payload["configuration"]["questions"] if item["code"] == code
        )

    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                data={"name": "Проверка групп"},
                files={"file": ("grouped.sav", stream, "application/octet-stream")},
            ).json()
            project_id = project["id"]
            grouped = next(
                item
                for item in project["configuration"]["questions"]
                if item["recognition"] == "auto_review"
            )
            code = grouped["code"]

            saved = client.patch(
                f"/api/projects/{project_id}/questions/{code}",
                json={"label": "Проверенный блок"},
            )
            assert saved.status_code == 200
            assert question(saved.json(), code)["recognition"] == "manual"

            refreshed = client.post(f"/api/projects/{project_id}/structure/refresh")
            assert refreshed.status_code == 200
            assert question(refreshed.json(), code)["recognition"] == "manual"
    finally:
        app.dependency_overrides.clear()


def test_saving_keeps_metadata_recognition_untouched(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                data={"name": "Метаданные"},
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()
            before = next(
                item
                for item in project["configuration"]["questions"]
                if item["code"] == "Q1"
            )["recognition"]
            assert before != "auto_review"

            updated = client.patch(
                f"/api/projects/{project['id']}/questions/Q1",
                json={"label": "Пол"},
            )
            assert updated.status_code == 200
            after = next(
                item
                for item in updated.json()["configuration"]["questions"]
                if item["code"] == "Q1"
            )["recognition"]
            assert after == before
    finally:
        app.dependency_overrides.clear()


def _write_weight_fixture(path: Path) -> None:
    """240 интервью: порядковый номер, группа и правдоподобный вес.

    Повторяет проверочный сценарий аудита 14 августа 2026 — там весом был
    сохранён именно `ID`.
    """
    rows = 240
    pyreadstat.write_sav(
        pd.DataFrame(
            {
                "ID": [float(index + 1) for index in range(rows)],
                "GROUP": [1 + index % 2 for index in range(rows)],
                "W": [1.4 if index % 3 else 0.8 for index in range(rows)],
            }
        ),
        path,
        variable_value_labels={"GROUP": {1: "Первая", 2: "Вторая"}},
        variable_measure={"GROUP": "nominal", "W": "scale"},
    )


def _weight_settings(variable: str | None) -> dict:
    return {
        "compare_to_total": False,
        "compare_target": "rest",
        "compare_pairwise": False,
        "confidence_level": 0.95,
        "bonferroni": False,
        "show_p_values": False,
        "minimum_base": 30,
        "weight_variable": variable,
        "calculated_weight_id": None,
        "wave_comparison": "none",
        "wave_control_value": None,
    }


def test_identifier_cannot_be_saved_as_a_report_weight(tmp_path: Path) -> None:
    """Сценарий аудита: `ID` весом не сохраняется ни до, ни после смены роли."""
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "weights.sav"
    _write_weight_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                data={"name": "Вес"},
                files={"file": ("weights.sav", stream, "application/octet-stream")},
            ).json()
            project_id = project["id"]

            rejected = client.put(
                f"/api/projects/{project_id}/report-settings",
                json=_weight_settings("ID"),
            )
            assert rejected.status_code == 422
            assert rejected.json()["error_code"] == "WEIGHT_ROLE_REQUIRED"

            declared = client.patch(
                f"/api/projects/{project_id}/questions/ID",
                json={"role": "weight"},
            )
            assert declared.status_code == 200
            question = next(
                item
                for item in declared.json()["configuration"]["questions"]
                if item["code"] == "ID"
            )
            assert question["role"] == "weight"
            # Объявленный весом вопрос уходит из отчёта: он больше не вопрос.
            assert question["included_in_report"] is False

            # Роль получена, и всё равно отказ: распределение порядкового номера
            # весом не является. Одной роли для защиты было бы мало.
            still_rejected = client.put(
                f"/api/projects/{project_id}/report-settings",
                json=_weight_settings("ID"),
            )
            assert still_rejected.status_code == 422
            assert still_rejected.json()["error_code"] == "WEIGHT_EXTREME_VALUES"

            diagnostics = client.get(
                f"/api/projects/{project_id}/weights/ready/ID/diagnostics"
            ).json()
            assert diagnostics["usable"] is False
            assert [item["code"] for item in diagnostics["problems"]] == [
                "WEIGHT_EXTREME_VALUES"
            ]
            assert diagnostics["diagnostics"]["count"] == 240
            assert diagnostics["diagnostics"]["maximum"] == 240.0
    finally:
        app.dependency_overrides.clear()


def test_declared_weight_passes_and_cannot_lose_its_role_while_selected(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "weights.sav"
    _write_weight_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                data={"name": "Вес"},
                files={"file": ("weights.sav", stream, "application/octet-stream")},
            ).json()
            project_id = project["id"]

            # `W` не попадает в список точных имён `infer_role`, поэтому роль
            # назначается явным действием — ради этого случая оно и заведено.
            assert (
                client.patch(
                    f"/api/projects/{project_id}/questions/W", json={"role": "weight"}
                ).status_code
                == 200
            )
            accepted = client.put(
                f"/api/projects/{project_id}/report-settings",
                json=_weight_settings("W"),
            )
            assert accepted.status_code == 200
            assert accepted.json()["configuration"]["report_settings"]["weight_variable"] == "W"

            diagnostics = client.get(
                f"/api/projects/{project_id}/weights/ready/W/diagnostics"
            ).json()
            assert diagnostics["usable"] is True
            assert diagnostics["problems"] == []
            assert diagnostics["diagnostics"]["design_effect"] < 1.1

            preflight = client.get(f"/api/projects/{project_id}/reports/preflight").json()
            assert preflight["can_prepare"] is True

            # Снять роль с выбранного веса нельзя: отчёт остался бы настроенным
            # на переменную, которую сборка уже отвергает.
            locked = client.patch(
                f"/api/projects/{project_id}/questions/W", json={"role": "question"}
            )
            assert locked.status_code == 422
            assert "выбрана весом отчёта" in locked.json()["detail"]

            released = client.put(
                f"/api/projects/{project_id}/report-settings",
                json=_weight_settings(None),
            )
            assert released.status_code == 200
            assert (
                client.patch(
                    f"/api/projects/{project_id}/questions/W", json={"role": "question"}
                ).status_code
                == 200
            )
    finally:
        app.dependency_overrides.clear()
