"""Волны отдельными источниками (PQ.19, решение 034).

Первая волна — исходный файл проекта, вторая приходит своим файлом с
переименованной переменной, новым кодом и новой переменной. Проверяется
сопоставление (имя, подпись, ИИ, человек), сходимость, общий массив с
переменной волны и разрез по волнам в таблице и на листе «Тренды».
"""

import io
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.api_dependencies import get_long_chat_model
from sav_analytics.assistant.models import ModelReply, ToolCall, ToolSpec
from sav_analytics.core.reporting.builder import build_topline_xlsx
from sav_analytics.core.reporting.live import build_live_table
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture


def write_second_wave(path: Path) -> None:
    frame = pd.DataFrame(
        {
            "id": [201, 202, 203],
            "Q1": [1, 3, 2],
            "SCORE": [8, 10, 6],
            "Q3_1": [1, 1, 0],
            "Q3_2": [0, 1, 1],
            "Q4_open": ["Отлично", "Дорого", "Быстро"],
            "Q9": [1, 2, 1],
            "BRANDX": [1, 0, 1],
        }
    )
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={
            "id": "Номер интервью",
            "Q1": "Ваш пол?",
            "SCORE": "Оцените сервис от 0 до 10",
            "Q3_1": "Что понравилось: скорость",
            "Q3_2": "Что понравилось: удобство",
            "Q4_open": "Почему вы поставили такую оценку?",
            "Q9": "Новый вопрос волны",
            "BRANDX": "Знание бренда X",
        },
        variable_value_labels={"Q1": {1: "Мужчина", 2: "Женщина", 3: "Другое"}},
        variable_measure={"id": "nominal", "Q1": "nominal", "SCORE": "scale"},
    )


class MatchModel:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> ModelReply:
        self.requests.append(messages[0]["content"])
        pairs = [{"target": "Q3_2", "source": "BRANDX"}, {"target": "NOPE", "source": "Q9"}]
        return ModelReply(
            content=None, tool_calls=[ToolCall("c1", "submit_matches", {"pairs": pairs})]
        )


@pytest.fixture
def project(tmp_path: Path) -> Iterator[dict]:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    context: dict = {"repository": repository, "model": MatchModel(), "tmp": tmp_path}
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_long_chat_model] = lambda: context["model"]
    first = tmp_path / "wave1.sav"
    second = tmp_path / "wave2.sav"
    write_fixture(first)
    write_second_wave(second)
    try:
        with TestClient(app) as client, first.open("rb") as stream:
            created = client.post(
                "/api/projects",
                files={"file": ("wave1.sav", stream, "application/octet-stream")},
            ).json()
            context.update(
                client=client,
                project_id=created["id"],
                base=f"/api/projects/{created['id']}",
                second=second,
            )
            yield context
    finally:
        app.dependency_overrides.clear()


def _stage(context: dict) -> dict:
    with context["second"].open("rb") as stream:
        response = context["client"].post(
            f"{context['base']}/waves/stage",
            files={"file": ("wave2.sav", stream, "application/octet-stream")},
        )
    assert response.status_code == 200, response.text
    return response.json()


def _add(context: dict, preview: dict, mapping: dict | None = None, added=("Q9",)) -> dict:
    response = context["client"].post(
        f"{context['base']}/waves",
        json={
            "staging_id": preview["staging_id"],
            "label": "Волна 2",
            "mapping": mapping or {},
            "added": list(added),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _row(preview: dict, target: str) -> dict:
    return next(row for row in preview["mapping"] if row["target"] == target)


def test_mapping_by_code_and_label_and_convergence(project) -> None:
    preview = _stage(project)
    assert _row(preview, "Q1") | {"label": ""} == {
        "target": "Q1", "label": "", "source": "Q1", "how": "code"
    }
    assert _row(preview, "Q2")["source"] == "SCORE"
    assert _row(preview, "Q2")["how"] == "label"
    unmatched = {item["name"]: item["can_add"] for item in preview["unmatched"]}
    assert unmatched == {"Q9": True, "BRANDX": True}
    changed = {item["name"]: item["notes"] for item in preview["convergence"]["changed"]}
    assert "новые коды: 3" in changed["Q1"]
    assert preview["convergence"]["blocking"] == []


def test_type_mismatch_blocks_the_wave(project) -> None:
    preview = _stage(project)
    response = project["client"].post(
        f"{project['base']}/waves",
        json={"staging_id": preview["staging_id"], "label": "Волна 2",
              "mapping": {"Q2": "Q4_open"}},
    )
    assert response.status_code == 422
    assert "число" in response.json()["detail"]


def test_added_wave_stacks_rows_with_wave_variable(project) -> None:
    preview = _stage(project)
    result = _add(project, preview)
    assert [wave["label"] for wave in result["waves"]] == ["Волна 1", "Волна 2"]
    assert result["waves_meta"]["variable"] == "WAVE"
    questions = {item["code"]: item for item in result["configuration"]["questions"]}
    assert questions["WAVE"]["role"] == "wave"
    assert questions["WAVE"]["included_in_report"] is False
    assert "Q9" in questions

    repository = project["repository"]
    frame, meta = pyreadstat.read_sav(repository.source_path(project["project_id"]))
    assert len(frame) == 7
    assert frame["WAVE"].tolist() == [1, 1, 1, 1, 2, 2, 2]
    # SCORE второй волны лёг в Q2 проекта.
    assert frame["Q2"].tolist()[4:] == [8, 10, 6]
    assert frame["Q9"].isna().sum() == 4
    assert meta.variable_value_labels["Q1"][3.0] == "Другое"
    assert meta.variable_value_labels["WAVE"] == {1.0: "Волна 1", 2.0: "Волна 2"}


def test_wave_is_a_column_and_missing_question_has_zero_base(project) -> None:
    preview = _stage(project)
    _add(project, preview, mapping={"Q3_2": None})
    repository = project["repository"]
    stored = repository.get(project["project_id"])
    live = build_live_table(
        repository.source_path(project["project_id"]),
        stored,
        questions=["Q1", "Q9"],
        blocks=[{"label": "Волна", "sources": [{"kind": "question", "ref": "WAVE"}]}],
    )
    assert [column["label"] for column in live["columns"]] == ["Total", "Волна 1", "Волна 2"]
    assert [column["base"] for column in live["columns"]] == [7, 4, 3]
    frame, _ = pyreadstat.read_sav(repository.source_path(project["project_id"]))
    # Q3_2 во второй волне не сопоставлен — «не задавался»: значений нет.
    assert frame["Q3_2"].tolist()[4:] == [None, None, None] or frame["Q3_2"][4:].isna().all()


def test_workbook_gets_trend_sheet(project) -> None:
    _add(project, _stage(project))
    repository = project["repository"]
    stored = repository.get(project["project_id"])
    content = build_topline_xlsx(repository.source_path(project["project_id"]), stored)
    archive = zipfile.ZipFile(io.BytesIO(content))
    assert 'name="Тренды"' in archive.read("xl/workbook.xml").decode("utf-8")
    assert "Волна 2" in archive.read("xl/sharedStrings.xml").decode("utf-8")


def test_ai_suggests_pairs_only_for_free_variables(project) -> None:
    preview = _stage(project)
    job = project["client"].post(
        f"{project['base']}/waves/stage/{preview['staging_id']}/ai-match",
        json={"mapping": {"Q3_2": None}},
    ).json()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        done = project["client"].get(f"{project['base']}/jobs/{job['job_id']}").json()
        if done["status"] in {"complete", "failed"}:
            break
        time.sleep(0.02)
    assert done["status"] == "complete", done
    assert done["result"]["pairs"] == [{"target": "Q3_2", "source": "BRANDX"}]
    assert "Знание бренда X" in project["model"].requests[0]


def test_rename_remove_and_duplicate(project) -> None:
    _add(project, _stage(project))
    waves = project["client"].get(f"{project['base']}/waves").json()["waves"]
    renamed = project["client"].patch(
        f"{project['base']}/waves/{waves[1]['id']}", json={"label": "Октябрь"}
    ).json()
    assert renamed["waves"][1]["label"] == "Октябрь"
    copy = project["client"].post(f"{project['base']}/duplicate").json()
    copied = project["repository"].get(copy["id"])
    assert len(copied["waves"]) == 2
    assert project["client"].delete(
        f"{project['base']}/waves/{waves[0]['id']}"
    ).status_code == 422
    removed = project["client"].delete(f"{project['base']}/waves/{waves[1]['id']}").json()
    assert len(removed["waves"]) == 1
    frame, _ = pyreadstat.read_sav(project["repository"].source_path(project["project_id"]))
    assert len(frame) == 4
