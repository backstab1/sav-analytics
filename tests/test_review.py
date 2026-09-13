"""Единое состояние «требует проверки» (P1.0, GAP-002).

Раньше эвристическая шкала показывала предупреждение «требует проверки» и
статус «Готов» одновременно: статус смотрел на `auto_review`, который ставится
только автоматически собранным группам. Эти тесты доказывают, что статус,
preflight и подтверждение теперь читают один признак.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyreadstat
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.review import question_needs_review, with_review_state
from sav_analytics.repository import ProjectRepository

SCALE_WARNING = "Тип шкалы определён по диапазону значений и требует проверки."


def _write_survey(path: Path) -> None:
    """SEX подписан метаданными, SCORE — шкала без подписей, узнанная по диапазону."""
    size = 60
    frame = pd.DataFrame(
        {
            "SEX": [1 + index % 2 for index in range(size)],
            "SCORE": [1 + index % 5 for index in range(size)],
        }
    )
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={"SEX": "Пол", "SCORE": "Оценка"},
        variable_value_labels={"SEX": {1: "Мужчина", 2: "Женщина"}},
        variable_measure={"SEX": "nominal", "SCORE": "scale"},
    )


def _question(project: dict, code: str) -> dict:
    return next(
        item for item in project["configuration"]["questions"] if item["code"] == code
    )


def _codes(findings: list[dict]) -> set[str]:
    return {item["code"] for item in findings}


def test_heuristic_scale_is_pending_until_saved_and_stays_confirmed(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "survey.sav"
    _write_survey(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                data={"name": "Проверка распознавания"},
                files={"file": ("survey.sav", stream, "application/octet-stream")},
            ).json()
            project_id = project["id"]
            score = _question(project, "SCORE")

            # Предупреждение и «готов» больше не могут стоять рядом.
            assert SCALE_WARNING in score["warnings"]
            assert score["needs_review"] is True
            assert _question(project, "SEX")["needs_review"] is False

            preflight = client.get(f"/api/projects/{project_id}/reports/preflight").json()
            assert "QUESTIONS_NEED_REVIEW" in _codes(preflight["warnings"])
            assert "SCORE" in next(
                item["message"]
                for item in preflight["warnings"]
                if item["code"] == "QUESTIONS_NEED_REVIEW"
            )
            # Предупреждение, а не отказ: массив без подписей иначе не собрать.
            assert preflight["can_prepare"] is True

            saved = client.patch(
                f"/api/projects/{project_id}/questions/SCORE", json={"label": "Оценка"}
            )
            assert saved.status_code == 200
            assert _question(saved.json(), "SCORE")["needs_review"] is False
            assert _question(saved.json(), "SCORE")["recognition"] == "manual"

            preflight = client.get(f"/api/projects/{project_id}/reports/preflight").json()
            assert "QUESTIONS_NEED_REVIEW" not in _codes(preflight["warnings"])

            refreshed = client.post(f"/api/projects/{project_id}/structure/refresh")
            assert _question(refreshed.json(), "SCORE")["needs_review"] is False
            reopened = client.get(f"/api/projects/{project_id}").json()
            assert _question(reopened, "SCORE")["needs_review"] is False

        # Признак вычисляется на выходе API и в хранилище не попадает: иначе он
        # стал бы второй правдой рядом с `recognition` и сдвинул ключ кэша.
        stored = json.loads(
            (tmp_path / "projects" / project_id / "project.json").read_text(encoding="utf-8")
        )
        assert all("needs_review" not in item for item in stored["configuration"]["questions"])
    finally:
        app.dependency_overrides.clear()


def test_excluded_or_confirmed_questions_never_need_review() -> None:
    pending = {"included_in_report": True, "recognition": "auto", "warnings": [SCALE_WARNING]}
    assert question_needs_review(pending) is True
    assert question_needs_review({**pending, "included_in_report": False}) is False
    assert question_needs_review({**pending, "recognition": "manual"}) is False
    assert question_needs_review({**pending, "recognition": "metadata"}) is False
    assert question_needs_review({**pending, "warnings": []}) is False
    # Автоматически собранная группа — частный случай того же правила.
    group = {
        "included_in_report": True,
        "recognition": "auto_review",
        "warnings": ["Автоматически собранная группа."],
    }
    assert question_needs_review(group) is True


def test_presentation_does_not_touch_the_stored_dictionary() -> None:
    project = {
        "configuration": {
            "questions": [
                {"code": "Q1", "included_in_report": True, "warnings": ["x"]},
            ]
        }
    }
    presented = with_review_state(project)
    assert presented["configuration"]["questions"][0]["needs_review"] is True
    assert "needs_review" not in project["configuration"]["questions"][0]


def test_new_warning_after_refresh_asks_for_review_again(tmp_path: Path) -> None:
    """Подтверждали конкретные предупреждения; новое нужно подтвердить заново."""
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    source = tmp_path / "survey.sav"
    _write_survey(source)
    with source.open("rb") as stream:
        project = repository.create("Повторная проверка", "survey.sav", stream)
    project_id = project["id"]
    confirmed = repository.update_question(project_id, "SCORE", {"label": "Оценка"})
    assert question_needs_review(_question(confirmed, "SCORE")) is False

    # Имитируем подтверждение, сделанное до появления нынешнего предупреждения.
    metadata_path = tmp_path / "projects" / project_id / "project.json"
    stored = json.loads(metadata_path.read_text(encoding="utf-8"))
    _question(stored, "SCORE")["warnings"] = []
    metadata_path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

    refreshed = repository.refresh_structure(project_id)
    assert question_needs_review(_question(refreshed, "SCORE")) is True
