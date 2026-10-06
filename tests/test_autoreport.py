"""Автоотчёт (PQ.18, решение 034): бриф → план → числа ядра → текст → DOCX.

Модель подменена: проверяется не качество текста, а то, что план
проверяется сервером, числа берутся из ядра, лишние проценты в тексте
помечаются, правки человека переживают пересборку, а DOCX — валидный
документ с родными диаграммами.
"""

import io
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.api_dependencies import get_long_chat_model
from sav_analytics.assistant.models import ModelReply, ToolCall, ToolSpec
from sav_analytics.core import autoreport
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture

PLAN = {
    "clarifications": [{"question": "Кто основной конкурент?", "options": ["А", "Б"]}],
    "banner": ["Q1", "Q2", "NOPE"],
    "weight": "",
    "sections": [
        {"title": "Оценка сервиса", "goal": "Как оценивают", "questions": ["Q2", "Q3"]},
        {"title": "Пустой", "questions": ["NOPE"]},
    ],
    "notes": "План по брифу",
}


class TextModel:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.texts: dict = {}

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> ModelReply:
        tool = tools[0].name
        self.requests.append((tool, messages[0]["content"]))
        arguments = PLAN if tool == "submit_plan" else self.texts
        return ModelReply(content=None, tool_calls=[ToolCall("c1", tool, arguments)])


@pytest.fixture
def project(tmp_path: Path) -> Iterator[dict]:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    model = TextModel()
    context: dict = {"repository": repository, "model": model}
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_long_chat_model] = lambda: context["model"]
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            created = client.post(
                "/api/projects",
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()
            context.update(
                client=client,
                project_id=created["id"],
                base=f"/api/projects/{created['id']}/autoreport",
                project_base=f"/api/projects/{created['id']}",
            )
            yield context
    finally:
        app.dependency_overrides.clear()


def _wait(context: dict, job_id: str) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = context["client"].get(f"{context['project_base']}/jobs/{job_id}").json()
        if job["status"] in {"complete", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("Задача не завершилась")


def _plan(context: dict) -> dict:
    context["client"].put(
        f"{context['base']}/brief",
        json={"tasks": "Понять оценку сервиса", "description": "Опрос клиентов",
              "object": "Наш сервис"},
    )
    job = context["client"].post(f"{context['base']}/plan").json()
    done = _wait(context, job["job_id"])
    assert done["status"] == "complete", done
    return done


def _build(context: dict, overwrite: list[str] | None = None) -> dict:
    state = context["client"].get(context["base"]).json()
    section_id = state["plan"]["sections"][0]["id"]
    context["model"].texts = {
        "summary": ["Высокие оценки у половины: 50%.", "Выдумка: 77%."],
        "sections": [{"id": section_id, "text": "Оценку 10 поставили 25%, а 99% — нет."}],
        "conclusion": "Сервис оценивают хорошо.",
    }
    job = context["client"].post(
        f"{context['base']}/build", json={"overwrite": overwrite or []}
    ).json()
    done = _wait(context, job["job_id"])
    assert done["status"] == "complete", done
    return context["client"].get(context["base"]).json()


def test_plan_is_checked_against_the_project(project) -> None:
    done = _plan(project)
    assert done["result"]["sections"] == 1
    assert any("NOPE" in warning for warning in done["result"]["warnings"])
    state = project["client"].get(project["base"]).json()
    plan = state["plan"]
    # Разрез — вопросы с вариантами ответа: шкала и несуществующий отброшены.
    assert plan["banner"] == ["Q1"]
    assert any("Q2 не подходит" in warning for warning in done["result"]["warnings"])
    assert [section["title"] for section in plan["sections"]] == ["Оценка сервиса"]
    assert state["clarifications"][0]["id"] == "q1"
    tool, content = project["model"].requests[0]
    assert tool == "submit_plan"
    assert "Наш сервис" in content and '"code": "Q2"' in content


def test_plan_needs_enabled_ai(project) -> None:
    project["client"].put(f"{project['project_base']}/ai", json={"enabled": False})
    response = project["client"].post(f"{project['base']}/plan")
    assert response.status_code == 409


def test_edited_plan_and_answers_are_saved(project) -> None:
    _plan(project)
    state = project["client"].get(project["base"]).json()
    section = state["plan"]["sections"][0]
    response = project["client"].put(
        f"{project['base']}/plan",
        json={
            "banner": ["Q1"],
            "sections": [{**section, "questions": ["Q2"]}],
            "answers": {"q1": "Конкурент А", "q9": "лишний"},
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["plan"]["sections"][0]["id"] == section["id"]
    assert saved["plan"]["sections"][0]["questions"] == ["Q2"]
    assert saved["answers"] == {"q1": "Конкурент А"}


def test_build_uses_core_numbers_and_flags_invented_ones(project) -> None:
    _plan(project)
    state = _build(project)
    report = state["report"]
    section = report["sections"][0]
    assert [card["code"] for card in section["cards"]] == ["Q2", "Q3"]
    table = section["cards"][0]["table"]
    assert table["columns"][0]["label"] == "Total"
    assert table["columns"][0]["base"] == 4
    # Q2: 10, 9, 7 и пропуск — по 25% от всех четверых.
    ten = next(row for row in table["rows"] if row["label"].startswith("10"))
    assert ten["cells"][0]["value"] == 25
    assert section["warnings"] == ["99%"]
    assert report["summary"]["warnings"] == ["77%"]
    assert "Значимость различий" in report["method"]

    # План применён к проекту обычными настройками: баннер «Автоотчёт».
    project_state = project["client"].get(project["project_base"]).json()
    configuration = project_state["configuration"]
    banner = next(item for item in configuration["banners"] if item.get("autoreport"))
    assert configuration["report_banner_id"] == banner["id"]
    assert [block["sources"][0]["ref"] for block in banner["blocks"]] == ["Q1"]

    texts_request = project["model"].requests[-1]
    assert texts_request[0] == "submit_texts"
    assert "Понять оценку сервиса" in texts_request[1]


def test_rebuild_keeps_human_edits_unless_overwritten(project) -> None:
    _plan(project)
    state = _build(project)
    section_id = state["report"]["sections"][0]["id"]
    edited = project["client"].put(
        f"{project['base']}/report",
        json={"summary": "Мой вывод", "sections": [
            {"id": section_id, "text": "Мой текст", "hidden": ["Q3"], "charts": {"Q2": "column"}}
        ]},
    ).json()
    assert {block["id"] for block in edited["edited_blocks"]} == {"summary", section_id}

    rebuilt = _build(project)["report"]
    assert rebuilt["summary"]["text"] == "Мой вывод"
    section = rebuilt["sections"][0]
    assert section["text"] == "Мой текст" and section["edited"] is True
    assert {card["code"]: card["hidden"] for card in section["cards"]} == {"Q2": False, "Q3": True}
    assert section["cards"][0]["chart"] == "column"
    # Повторная сборка не плодит баннеры.
    banners = project["client"].get(project["project_base"]).json()["configuration"]["banners"]
    assert sum(1 for item in banners if item.get("autoreport")) == 1

    overwritten = _build(project, [section_id])["report"]
    assert overwritten["sections"][0]["text"].startswith("Оценку 10")
    assert overwritten["summary"]["text"] == "Мой вывод"


def test_docx_is_a_valid_document_with_editable_charts(project) -> None:
    _plan(project)
    _build(project)
    response = project["client"].get(f"{project['base']}/report.docx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml"
    )
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    assert {"word/document.xml", "word/charts/chart1.xml",
            "word/embeddings/Microsoft_Excel_Worksheet1.xlsx"} <= names
    for name in names:
        if name.endswith((".xml", ".rels")):
            ElementTree.fromstring(archive.read(name))
    document = archive.read("word/document.xml").decode("utf-8")
    assert "Ключевые выводы" in document and "Оценка сервиса" in document
    assert "Значимость различий" in document
    chart = archive.read("word/charts/chart1.xml").decode("utf-8")
    assert "<c:v>25</c:v>" in chart
    embedded = zipfile.ZipFile(io.BytesIO(archive.read(
        "word/embeddings/Microsoft_Excel_Worksheet1.xlsx")))
    assert "xl/worksheets/sheet1.xml" in embedded.namelist()


def test_report_order_and_unknown_section(project) -> None:
    _plan(project)
    state = _build(project)
    section_id = state["report"]["sections"][0]["id"]
    assert project["client"].put(
        f"{project['base']}/report", json={"order": [section_id]}
    ).status_code == 200
    assert project["client"].put(
        f"{project['base']}/report", json={"sections": [{"id": "x", "text": "?"}]}
    ).status_code == 404


def test_docx_before_build_is_404(project) -> None:
    assert project["client"].get(f"{project['base']}/report.docx").status_code == 404


def test_unverified_numbers_allow_values_and_differences() -> None:
    tables = [{"rows": [{"cells": [{"value": 40.0}, {"value": 52.4}]}]}]
    assert autoreport.unverified_numbers("40% против 52%, разница 12 п.п.", tables) == []
    assert autoreport.unverified_numbers("а 70% — нет", tables) == ["70%"]
