"""Открытые ответы на ИИ (PQ.17, решение 034).

Модель подменена правилами: справочник фиксированный, коды ставятся по
словам ответа. Проверяется ядро: коды привязаны к тексту ответа, правка
человека пишется в словарь и переживает перекодирование, редкие коды
уходят в «Другое», код — вопрос multiple-response в книге и в отмене.
"""

import json
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics import coding_jobs
from sav_analytics.api import app, get_repository
from sav_analytics.api_dependencies import get_fast_chat_model, get_long_chat_model
from sav_analytics.assistant.models import ModelError, ModelReply, ToolCall, ToolSpec
from sav_analytics.coding_jobs import codebook_themes, sample_answers
from sav_analytics.core.open_text import (
    answer_rows,
    codeframe_summary,
    merge_rare_codes,
    normalize_answer,
    unique_answers,
)
from sav_analytics.core.report import build_topline_xlsx
from sav_analytics.repository import ProjectRepository
from tests.test_report import _cell_value, _row_labels

ANSWERS = [
    "Очень доволен доставкой",
    "Доставку привезли быстро",
    "Дорого, но качественно",
    "Цены высокие",
    "",
    "Довольна качеством и ценой",
    "Курьер опоздал с доставкой",
    "Ничего не понравилось",
    "цены  высокие",
    "Дорого",
]

CODEBOOK = {
    "codes": [
        {"name": "Быстрая доставка", "group": "Доставка", "description": "о скорости"},
        {"name": "Курьер", "group": "Доставка"},
        {"name": "Высокие цены", "group": "Цена"},
        {"name": "Качество"},
        {"name": "Ничего не понравилось"},
    ]
}

RULES = {
    "Быстрая доставка": ("доставк", "быстро"),
    "Курьер": ("курьер",),
    "Высокие цены": ("дорого", "цен"),
    "Качество": ("качеств",),
    "Ничего не понравилось": ("ничего",),
}


def _rule_tone(text: str) -> str:
    if "но " in text:
        return "mixed"
    if any(word in text for word in ("дорог", "высок", "ничего", "опоздал")):
        return "negative"
    if any(word in text for word in ("довол", "быстро")):
        return "positive"
    return "neutral"


class RuleModel:
    """Справочник по запросу, коды и тон по словам ответа."""

    def __init__(self, codebook: dict | None = None, fail_codes: int = 0) -> None:
        self.codebook = codebook or CODEBOOK
        self.fail_codes = fail_codes
        self.calls: list[str] = []
        self.coded_texts: list[str] = []
        self.systems: list[str] = []
        self._lock = threading.Lock()

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> ModelReply:
        tool = tools[0].name
        with self._lock:
            self.calls.append(tool)
        if tool == "submit_codebook":
            return _call(tool, self.codebook)
        with self._lock:
            if self.fail_codes:
                self.fail_codes -= 1
                raise ModelError("таймаут")
        content = messages[0]["content"]
        with self._lock:
            self.systems.append(system)
        toned = "tone" in tools[0].parameters["properties"]["items"]["items"]["properties"]
        numbers = dict(
            (name, int(number))
            for number, name in re.findall(r"^(\d+)\. ([^—\n]+?)(?: —|$)", content, re.M)
        )
        answers = json.loads(content[content.index("Ответы:\n") + len("Ответы:\n"):])
        items = []
        for answer in answers:
            text = answer["text"].lower()
            with self._lock:
                self.coded_texts.append(answer["text"])
            codes = [
                numbers[name] for name, words in RULES.items()
                if name in numbers and any(word in text for word in words)
            ]
            item = {"i": answer["i"], "codes": codes, "low_confidence": "но" in text}
            if toned:
                item["tone"] = _rule_tone(text)
            items.append(item)
        return _call(tool, {"items": items})


def _call(name: str, arguments: dict) -> ModelReply:
    return ModelReply(content=None, tool_calls=[ToolCall("c1", name, arguments)])


def _write(path: Path) -> None:
    pyreadstat.write_sav(
        pd.DataFrame({"ID": list(range(1, len(ANSWERS) + 1)), "WHY": ANSWERS}),
        path,
        column_labels={"ID": "Номер", "WHY": "Почему вы так оценили?"},
        variable_measure={"ID": "nominal"},
    )


@pytest.fixture
def project(tmp_path: Path) -> Iterator[dict]:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    context: dict = {"repository": repository, "model": RuleModel()}
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_long_chat_model] = lambda: context["model"]
    app.dependency_overrides[get_fast_chat_model] = lambda: context["model"]
    source = tmp_path / "open.sav"
    _write(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            created = client.post(
                "/api/projects", files={"file": ("open.sav", stream, "application/octet-stream")}
            ).json()
            base = f"/api/projects/{created['id']}"
            client.patch(f"{base}/questions/WHY", json={"question_type": "open_text"})
            context.update(client=client, project_id=created["id"], base=base)
            yield context
    finally:
        app.dependency_overrides.clear()


def _wait(context: dict, job_id: str) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = context["client"].get(f"{context['base']}/jobs/{job_id}").json()
        if job["status"] in {"complete", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("Задача не завершилась")


def _code(context: dict) -> tuple[dict, dict]:
    response = context["client"].post(
        f"{context['base']}/codeframes/batch", json={"question_codes": ["WHY"]}
    )
    assert response.status_code == 200, response.text
    job = _wait(context, response.json()["jobs"][0]["job_id"])
    assert job["status"] == "complete", job
    project = context["client"].get(context["base"]).json()
    return project, project["configuration"]["codeframes"][0]


def _url(context: dict, codeframe: dict) -> str:
    return f"{context['base']}/codeframes/{codeframe['id']}"


def _themes(codeframe: dict) -> dict[str, dict]:
    return {theme["name"]: theme for theme in codeframe["themes"]}


def test_answers_are_keyed_by_normalized_text() -> None:
    assert normalize_answer("  Цены  ВЫСОКИЕ ") == normalize_answer("цены высокие")
    assert normalize_answer("Ёлка") == "елка"
    unique = unique_answers(pd.Series(ANSWERS))
    assert unique[0][1:] == ("Цены высокие", 2)
    assert len(unique) == 8


def test_codebook_groups_become_parents_and_keep_preserves_codes() -> None:
    themes, next_number = codebook_themes(CODEBOOK, [], 1)
    names = {theme["name"]: theme for theme in themes}
    assert names["Курьер"]["parent_id"] == names["Доставка"]["id"]
    # Группа из одного кода не создаётся: «Высокие цены» — без группы «Цена».
    assert "Цена" not in names
    assert names["Высокие цены"]["parent_id"] is None
    assert next_number == 7

    revised, _ = codebook_themes(
        {"codes": [{"name": "Цена слишком высокая", "keep": names["Высокие цены"]["number"]}]},
        themes,
        next_number,
    )
    assert revised[0]["id"] == names["Высокие цены"]["id"]
    assert revised[0]["name"] == "Цена слишком высокая"


def test_sample_keeps_frequent_answers_first() -> None:
    unique = [(f"k{i}", f"t{i}", 1000 - i) for i in range(1000)]
    sample = sample_answers(unique)
    assert len(sample) == 400
    assert sample[0] == ("t0", 1000)


def test_rare_codes_merge_into_other() -> None:
    themes = [
        {"id": "a", "number": 1, "name": "Частый", "parent_id": None},
        {"id": "b", "number": 2, "name": "Редкий", "parent_id": None},
    ]
    coding = {"answers": {"x": {"codes": ["a"]}, "y": {"codes": ["b", "a"]}}, "dictionary": {}}
    kept, coding, next_number = merge_rare_codes(
        themes, coding, {"a": 99, "b": 1}, 100, 0.05, lambda: "other", 3
    )
    assert [theme["name"] for theme in kept] == ["Частый", "Другое"]
    assert coding["answers"]["y"]["codes"] == ["a", "other"]
    assert next_number == 4


def test_coding_needs_enabled_ai(project) -> None:
    project["client"].put(f"{project['base']}/ai", json={"enabled": False})
    response = project["client"].post(
        f"{project['base']}/codeframes/batch", json={"question_codes": ["WHY"]}
    )
    assert response.status_code == 409
    assert project["model"].calls == []


def test_candidates_list_open_questions(project) -> None:
    candidates = project["client"].get(f"{project['base']}/codeframes/candidates").json()
    assert [item["code"] for item in candidates["questions"]] == ["WHY"]
    assert candidates["questions"][0]["respondent_answers"] is True


def test_batch_builds_codebook_and_codes_unique_answers_once(project) -> None:
    built, codeframe = _code(project)
    names = _themes(codeframe)
    assert set(names) == {
        "Доставка", "Быстрая доставка", "Курьер", "Высокие цены", "Качество",
        "Ничего не понравилось",
    }
    # Справочник строит основная модель один раз, коды — пачкой; одинаковые
    # ответы «Цены высокие» и «цены  высокие» уходят модели один раз.
    assert project["model"].calls[0] == "submit_codebook"
    assert len(project["model"].coded_texts) == 8

    question = next(
        item for item in built["configuration"]["questions"] if item["code"] == codeframe["code"]
    )
    assert question["question_type"] == "multiple_choice_dichotomy"
    assert (question["valid_count"], question["missing_count"]) == (9, 1)

    summary = project["client"].get(f"{_url(project, codeframe)}/summary").json()
    counts = {item["id"]: item["count"] for item in summary["themes"]}
    assert counts[names["Высокие цены"]["id"]] == 5
    # Группа отмечена, если отмечен любой её код.
    assert counts[names["Доставка"]["id"]] == 3
    assert summary["tiles"] == {"unique": 8, "dictionary": 0, "ai": 8, "low": 2, "uncoded": 0}

    stored = project["repository"].get(project["project_id"])
    content = build_topline_xlsx(project["repository"].source_path(project["project_id"]), stored)
    assert "Высокие цены" in _row_labels(content)
    assert _cell_value(content, "Высокие цены", "B") == pytest.approx(5 / 10 * 100)


def test_manual_codes_go_to_dictionary_and_survive_recoding(project) -> None:
    _built, codeframe = _code(project)
    names = _themes(codeframe)
    url = _url(project, codeframe)
    edited = project["client"].put(
        f"{url}/answers",
        json={"key": "Дорого, но качественно", "codes": [names["Качество"]["id"]]},
    )
    assert edited.status_code == 200, edited.text
    rows = project["client"].get(f"{url}/answers", params={"view": "dictionary"}).json()
    assert [row["text"] for row in rows["rows"]] == ["Дорого, но качественно"]
    assert rows["rows"][0]["source"] == "manual"

    project["model"].coded_texts.clear()
    job = project["client"].post(f"{url}/code", json={"mode": "keep_edits"}).json()
    assert _wait(project, job["job_id"])["status"] == "complete"
    assert "Дорого, но качественно" not in project["model"].coded_texts
    assert len(project["model"].coded_texts) == 7
    row = project["client"].get(
        f"{url}/answers", params={"search": "качественно"}
    ).json()["rows"][0]
    assert row["codes"] == [names["Качество"]["id"]]

    # «Сбросить всё» очищает словарь: модель кодирует ответ заново.
    job = project["client"].post(f"{url}/code", json={"mode": "reset"}).json()
    assert _wait(project, job["job_id"])["status"] == "complete"
    reset = project["client"].get(f"{url}/answers", params={"view": "dictionary"}).json()
    assert reset["total"] == 0


def test_new_mode_codes_only_answers_without_codes(project) -> None:
    _built, codeframe = _code(project)
    project["model"].coded_texts.clear()
    job = project["client"].post(f"{_url(project, codeframe)}/code", json={"mode": "new"}).json()
    done = _wait(project, job["job_id"])
    assert done["status"] == "complete"
    assert project["model"].coded_texts == []


def test_failed_batch_is_retried_once(project) -> None:
    project["model"].fail_codes = 1
    _built, codeframe = _code(project)
    summary = project["client"].get(f"{_url(project, codeframe)}/summary").json()
    assert summary["tiles"]["uncoded"] == 0


def test_job_fails_when_every_batch_fails(project) -> None:
    project["model"].fail_codes = 10
    response = project["client"].post(
        f"{project['base']}/codeframes/batch", json={"question_codes": ["WHY"]}
    )
    job = _wait(project, response.json()["jobs"][0]["job_id"])
    assert job["status"] == "failed"
    assert job["error_code"] == "AI_PROVIDER_ERROR"


def test_deleted_code_leaves_answers_and_dictionary(project) -> None:
    _built, codeframe = _code(project)
    names = _themes(codeframe)
    url = _url(project, codeframe)
    project["client"].put(
        f"{url}/answers", json={"key": "Цены высокие", "codes": [names["Высокие цены"]["id"]]}
    )
    themes = [
        {"id": theme["id"], "name": theme["name"], "parent_id": theme["parent_id"]}
        for theme in codeframe["themes"] if theme["name"] != "Высокие цены"
    ]
    updated = project["client"].put(
        url, json={"label": codeframe["label"], "themes": themes, "instruction": "Бренды отдельно",
                   "multi": False}
    )
    assert updated.status_code == 200, updated.text
    stored = updated.json()["configuration"]["codeframes"][0]
    assert stored["instruction"] == "Бренды отдельно" and stored["multi"] is False
    exported = project["client"].get(f"{url}/export").json()
    assert exported["dictionary"] == {"цены высокие": []}
    assert names["Высокие цены"]["id"] not in {theme["id"] for theme in exported["themes"]}


def test_coding_is_one_undo_step(project) -> None:
    _built, codeframe = _code(project)
    names = _themes(codeframe)
    url = _url(project, codeframe)
    before = project["client"].get(f"{url}/answers", params={"search": "дорого"}).json()
    project["client"].put(
        f"{url}/answers", json={"key": "Дорого", "codes": [names["Качество"]["id"]]}
    )
    project["client"].post(f"{project['base']}/undo")
    after = project["client"].get(f"{url}/answers", params={"search": "дорого"}).json()
    assert after == before


def test_revision_returns_draft_without_saving(project) -> None:
    _built, codeframe = _code(project)
    names = _themes(codeframe)
    project["model"].codebook = {
        "codes": [
            {"name": "Цена", "keep": names["Высокие цены"]["number"]},
            {"name": "Сервис"},
        ]
    }
    job = project["client"].post(
        f"{_url(project, codeframe)}/revise", json={"request": "Объедини цену, добавь сервис"}
    ).json()
    done = _wait(project, job["job_id"])
    assert done["status"] == "complete"
    draft = done["result"]["themes"]
    assert draft[0]["id"] == names["Высокие цены"]["id"]
    assert draft[1]["id"].startswith("new-")
    unchanged = project["client"].get(project["base"]).json()["configuration"]["codeframes"][0]
    assert unchanged["themes"] == codeframe["themes"]


def test_import_maps_codes_by_name_and_brings_dictionary(project) -> None:
    _built, codeframe = _code(project)
    url = _url(project, codeframe)
    response = project["client"].post(
        f"{url}/import",
        json={
            "format": "sav-analytics/codeframe",
            "themes": [{"id": "x1", "name": "Качество"}, {"id": "x2", "name": "Упаковка"}],
            "dictionary": {"Ничего не понравилось": ["x2"]},
        },
    )
    assert response.status_code == 200, response.text
    names = _themes(response.json()["configuration"]["codeframes"][0])
    assert "Упаковка" in names
    row = project["client"].get(f"{url}/answers", params={"search": "ничего"}).json()["rows"][0]
    assert row["codes"] == [names["Упаковка"]["id"]]


def test_duplicate_project_keeps_codes(project) -> None:
    _built, codeframe = _code(project)
    copy = project["client"].post(f"{project['base']}/duplicate").json()
    copied = copy["configuration"]["codeframes"][0]
    rows = project["client"].get(
        f"/api/projects/{copy['id']}/codeframes/{copied['id']}/answers"
    ).json()
    assert all(row["codes"] for row in rows["rows"])


def test_summary_and_rows_without_coding() -> None:
    codeframe = {"themes": [{"id": "a", "number": 1, "name": "A", "parent_id": None}]}
    coding = {"answers": {}, "dictionary": {}}
    texts = pd.Series(["x", "y", ""])
    assert codeframe_summary(texts, codeframe, coding)["tiles"]["uncoded"] == 2
    assert answer_rows(texts, codeframe, coding, view="uncoded")["total"] == 2


def test_text_profile_separates_answers_from_service_fields() -> None:
    from sav_analytics.core.open_text import text_profile

    answers = text_profile(pd.Series(ANSWERS))
    logins = text_profile(pd.Series(["ivanov", "petrov", "2026-09-01 10:22", "", "+79001234567"]))

    assert answers["wordy_share"] > 0.8
    assert logins["wordy_share"] == 0
    assert answers["answered"] == 9


def test_service_fields_are_recognised_by_code_and_label() -> None:
    from sav_analytics.core.open_text import looks_like_service_field

    assert looks_like_service_field("UserName", "Имя пользователя")
    assert looks_like_service_field("ContactID", "ID контакта")
    assert looks_like_service_field("IVDate1", "Дата и время начала интервью")
    assert not looks_like_service_field("Q13", "Почему Вы поставили именно такую оценку?")
    assert not looks_like_service_field("Q20_2T", "Нет (уточните, почему не оформляет)")


assert coding_jobs.MODES == ("new", "keep_edits", "reset")


def test_revision_keeps_nested_codes_matched_by_name() -> None:
    themes, next_number = codebook_themes(CODEBOOK, [], 1)
    revised, _ = codebook_themes(CODEBOOK, themes, next_number)
    assert [theme["id"] for theme in revised] == [theme["id"] for theme in themes]


def test_tone_is_set_in_the_same_call_and_becomes_single_choice(project) -> None:
    built, codeframe = _code(project)
    assert codeframe["sentiment"] is True
    # Тон приходит тем же вызовом submit_codes: отдельного запроса нет.
    assert project["model"].calls.count("submit_codes") == 1
    assert "`tone`" in project["model"].systems[0]

    question = next(
        item for item in built["configuration"]["questions"]
        if item["code"] == f"{codeframe['code']}_TONE"
    )
    assert question["question_type"] == "single_choice"
    assert question["codeframe_tone"] is True
    assert (question["valid_count"], question["missing_count"]) == (9, 1)
    # Вопрос кодов находится по-прежнему, а не подменяется тональностью.
    codes = next(
        item for item in built["configuration"]["questions"] if item["code"] == codeframe["code"]
    )
    assert codes["question_type"] == "multiple_choice_dichotomy"
    order = [item["code"] for item in built["configuration"]["questions"]]
    assert order.index(question["code"]) == order.index(codes["code"]) + 1

    summary = project["client"].get(f"{_url(project, codeframe)}/summary").json()
    assert summary["tones"]["counts"] == {
        "positive": 3, "neutral": 0, "mixed": 1, "negative": 5,
    }
    assert summary["tones"]["untoned"] == 0

    stored = project["repository"].get(project["project_id"])
    content = build_topline_xlsx(project["repository"].source_path(project["project_id"]), stored)
    assert "Отрицательная" in _row_labels(content)
    # База — все респонденты, как у кодов того же вопроса.
    assert _cell_value(content, "Отрицательная", "B") == pytest.approx(5 / 10 * 100)


def test_manual_tone_goes_to_dictionary_and_filters_rows(project) -> None:
    _built, codeframe = _code(project)
    url = _url(project, codeframe)
    negative = project["client"].get(f"{url}/answers", params={"tone": "negative"}).json()
    assert {row["text"] for row in negative["rows"]} == {
        "Цены высокие", "Курьер опоздал с доставкой", "Ничего не понравилось", "Дорого",
    }
    edited = project["client"].put(
        f"{url}/answers/tone", json={"key": "дорого", "tone": "neutral"}
    )
    assert edited.status_code == 200, edited.text
    row = project["client"].get(f"{url}/answers", params={"search": "дорого"}).json()["rows"]
    row = next(item for item in row if item["text"] == "Дорого")
    assert (row["tone"], row["tone_source"]) == ("neutral", "manual")

    # Правка тона переживает перекодирование: модель тон не перебивает.
    job = project["client"].post(f"{url}/code", json={"mode": "keep_edits"}).json()
    assert _wait(project, job["job_id"])["status"] == "complete"
    summary = project["client"].get(f"{url}/summary").json()
    assert summary["tones"]["counts"]["neutral"] == 1

    exported = project["client"].get(f"{url}/export").json()
    assert exported["tones"] == {"дорого": "neutral"}

    cleared = project["client"].put(f"{url}/answers/tone", json={"key": "Дорого", "tone": None})
    assert cleared.status_code == 200
    summary = project["client"].get(f"{url}/summary").json()
    assert summary["tones"]["counts"]["neutral"] == 0


def test_turning_tone_on_later_tones_without_recoding(project) -> None:
    client = project["client"]
    created = client.post(f"{project['base']}/codeframes", json={"question_code": "WHY"})
    assert created.status_code == 201, created.text
    codeframe = created.json()["configuration"]["codeframes"][0]
    url = _url(project, codeframe)
    response = client.put(url, json={"label": codeframe["label"], "sentiment": False})
    assert response.status_code == 200, response.text
    job = client.post(f"{url}/code", json={"mode": "new"}).json()
    assert _wait(project, job["job_id"])["status"] == "complete"
    stored = client.get(project["base"]).json()
    assert not any(item.get("codeframe_tone") for item in stored["configuration"]["questions"])
    assert "tones" not in client.get(f"{url}/summary").json()
    refused = client.put(f"{url}/answers/tone", json={"key": "Дорого", "tone": "mixed"})
    assert refused.status_code == 422

    codeframe = stored["configuration"]["codeframes"][0]
    names = _themes(codeframe)
    edited = client.put(
        f"{url}/answers", json={"key": "Дорого", "codes": [names["Качество"]["id"]]}
    )
    assert edited.status_code == 200
    themes = [
        {"id": theme["id"], "name": theme["name"], "parent_id": theme["parent_id"]}
        for theme in codeframe["themes"]
    ]
    response = client.put(url, json={"label": codeframe["label"], "themes": themes,
                                     "sentiment": True})
    assert response.status_code == 200
    # Тональность включена, тонов ещё нет: вопрос есть, ответов в нём нет.
    question = next(
        item for item in response.json()["configuration"]["questions"]
        if item.get("codeframe_tone")
    )
    assert question["valid_count"] == 0

    project["model"].coded_texts.clear()
    job = client.post(f"{url}/code", json={"mode": "new"}).json()
    assert _wait(project, job["job_id"])["status"] == "complete"
    assert len(project["model"].coded_texts) == 8
    summary = client.get(f"{url}/summary").json()
    assert summary["tones"]["untoned"] == 0
    # Коды не перекодированы: правка человека осталась, источник — правка.
    row = next(
        item for item in client.get(f"{url}/answers", params={"view": "dictionary"}).json()["rows"]
        if item["text"] == "Дорого"
    )
    assert row["codes"] == [names["Качество"]["id"]]
    assert row["source"] == "manual"
    assert row["tone"] == "negative"


def test_tone_question_in_banner_blocks_turning_tone_off(project) -> None:
    _built, codeframe = _code(project)
    banner = project["client"].post(
        f"{project['base']}/banners",
        json={"name": "Тон", "blocks": [
            {"sources": [{"kind": "question", "ref": f"{codeframe['code']}_TONE"}]}
        ]},
    )
    assert banner.status_code in {200, 201}, banner.text
    themes = [
        {"id": theme["id"], "name": theme["name"], "parent_id": theme["parent_id"]}
        for theme in codeframe["themes"]
    ]
    response = project["client"].put(
        _url(project, codeframe),
        json={"label": codeframe["label"], "themes": themes, "sentiment": False},
    )
    assert response.status_code == 422
