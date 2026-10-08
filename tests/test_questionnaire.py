"""Анкета как источник подписей (PQ.16): текст файла, разбор моделью в
фоновой задаче, таблица «было → станет» и применение одной ревизией.

Модель подменена сценарием: проверяется не качество сопоставления, а то,
что ядро не доверяет модели — отбрасывает несуществующее, показывает только
изменения и применяет выбранное одним шагом отмены.
"""

import io
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.api_dependencies import get_long_chat_model
from sav_analytics.assistant.models import ModelError, ModelReply, ToolCall, ToolSpec
from sav_analytics.core.questionnaire import (
    QuestionnaireError,
    extract_text,
    reordered,
    value_key,
)
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture

QUESTIONNAIRE = """АНКЕТА
1. Ваш пол? 1 — Мужчина, 2 — Женщина
2. Насколько вероятно, что вы порекомендуете нас? Оценка от 0 до 10
3. Что вам понравилось? Скорость обслуживания; Удобство
4. Почему вы поставили такую оценку?
"""

MAPPING = {
    "questions": [
        {"code": "Q2", "label": "Насколько вероятно, что вы порекомендуете нас?",
         "question_type": "numeric"},
        {"code": "Q1", "label": "Ваш пол?"},
        {"code": "Q9", "label": "Такого вопроса нет"},
        {"code": "Q3", "question_type": "single_choice"},
    ],
    "variables": [
        {"name": "Q3_1", "label": "Скорость обслуживания"},
        {"name": "Q1", "values": [
            {"value": "1", "label": "Мужчина"},
            {"value": "2.0", "label": "Женщина, по анкете"},
            {"value": "5", "label": "Кода нет в данных"},
        ]},
        {"name": "Q2", "values": [{"value": "10", "label": "Точно порекомендую"}]},
        {"name": "NOPE", "label": "Нет такой"},
    ],
    "order": ["Q2", "Q1"],
    "notes": "Q4 не нашёл.",
}


class ScriptedModel:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.requests: list[tuple[str, list[dict], list[ToolSpec]]] = []

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> ModelReply:
        self.requests.append((system, [dict(item) for item in messages], tools))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _submit(arguments: dict) -> ModelReply:
    return ModelReply(content=None, tool_calls=[ToolCall("call-1", "submit_mapping", arguments)])


@pytest.fixture
def project(tmp_path: Path) -> Iterator[dict]:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    context: dict = {"repository": repository, "model": None}
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_long_chat_model] = lambda: context["model"]
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects",
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()["id"]
            context.update(client=client, project_id=project_id)
            context["base"] = f"/api/projects/{project_id}"
            yield context
    finally:
        app.dependency_overrides.pop(get_repository, None)
        app.dependency_overrides.pop(get_long_chat_model, None)


def _upload(context: dict, text: str = QUESTIONNAIRE, name: str = "anketa.txt"):
    return context["client"].post(
        f"{context['base']}/questionnaire",
        files={"file": (name, text.encode("utf-8"), "text/plain")},
    )


def _wait(context: dict, job_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = context["client"].get(f"{context['base']}/jobs/{job_id}").json()
        if job["status"] in {"complete", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("Задача не завершилась")


def _parsed(context: dict, mapping: dict = MAPPING) -> dict:
    context["model"] = ScriptedModel([_submit(mapping)])
    response = _upload(context)
    assert response.status_code == 200, response.text
    job = _wait(context, response.json()["job_id"])
    assert job["status"] == "complete", job
    return job


def _project(context: dict) -> dict:
    return context["client"].get(f"/api/projects/{context['project_id']}").json()


def _variable(project: dict, name: str) -> dict:
    return next(item for item in project["inspection"]["variables"] if item["name"] == name)


def _question(project: dict, code: str) -> dict:
    return next(item for item in project["configuration"]["questions"] if item["code"] == code)


def test_docx_text_keeps_paragraphs_and_table_rows() -> None:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document = (
        f'<w:document xmlns:w="{namespace}"><w:body>'
        "<w:p><w:r><w:t>1. Ваш пол?</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Мужчина</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:t>2. Возраст</w:t></w:r><w:r><w:tab/><w:t>лет</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    text = extract_text("Анкета.DOCX", buffer.getvalue())
    assert text.splitlines() == ["1. Ваш пол?", "1 | Мужчина", "2. Возраст лет"]


def test_text_file_is_read_in_cp1251_too() -> None:
    text = "Вопрос 1. Ваш пол? 1 — Мужчина"
    assert extract_text("a.txt", text.encode("cp1251")) == text


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("a.xlsx", b"x" * 100, "DOCX, PDF или TXT"),
        ("a.docx", b"not a zip" * 10, "повреждён"),
        ("a.pdf", b"%PDF-1.4 broken" * 10, "PDF"),
        ("a.txt", b"   ", "пуст"),
    ],
)
def test_unreadable_questionnaire_is_refused_with_reason(
    name: str, data: bytes, message: str
) -> None:
    with pytest.raises(QuestionnaireError, match=message):
        extract_text(name, data if data.strip() else b"")


def test_value_key_treats_number_forms_as_one_code() -> None:
    assert value_key(1) == value_key(1.0) == value_key("1") == value_key(" 1,0 ") == "1"
    assert value_key("Да") == "Да"
    assert value_key(1.5) != value_key(1)


def test_reorder_moves_only_named_questions_into_their_own_slots() -> None:
    assert reordered(["a", "x", "b", "y", "c"], ["c", "a", "b"]) == ["c", "x", "a", "y", "b"]


def test_questionnaire_needs_configured_model_and_enabled_ai(project) -> None:
    response = _upload(project)
    assert response.status_code == 503
    assert response.json()["error_code"] == "AI_NOT_CONFIGURED"

    project["model"] = ScriptedModel([])
    state = project["client"].put(f"{project['base']}/ai", json={"enabled": False}).json()
    assert state["enabled"] is False
    response = _upload(project)
    assert response.status_code == 409
    assert response.json()["error_code"] == "AI_DISABLED"
    assert project["model"].requests == []


def test_switching_ai_does_not_touch_configuration_or_history(project) -> None:
    before = _project(project)
    project["client"].put(f"{project['base']}/ai", json={"acknowledged": True})
    after = _project(project)
    assert after["ai"] == {"enabled": True, "acknowledged": True}
    assert after["configuration"]["questions"] == before["configuration"]["questions"]
    history = project["client"].get(f"{project['base']}/history").json()
    assert history["undo"] == 0


def test_model_sees_questionnaire_and_catalog_with_unlabeled_codes(project) -> None:
    _parsed(project)
    system, messages, tools = project["model"].requests[0]
    assert [tool.name for tool in tools] == ["submit_mapping"]
    content = messages[0]["content"]
    assert "Ваш пол? 1 — Мужчина" in content
    # Q2 без подписей кодов: модель видит встреченные значения, чтобы их подписать.
    assert '"name": "Q2"' in content and '"unlabeled_codes": [7.0, 9.0, 10.0]' in content


def test_rows_show_only_changes_and_skip_what_does_not_exist(project) -> None:
    result = _parsed(project)["result"]
    rows = {
        (row["kind"], row.get("question") or row.get("variable"), str(row.get("value", ""))): row
        for row in result["rows"]
    }
    assert ("question_label", "Q2", "") in rows
    # Подпись Q1 совпадает с анкетой — строки нет; «Мужчина» тоже не меняется.
    assert ("question_label", "Q1", "") not in rows
    assert ("value_label", "Q1", "1.0") not in rows
    assert rows[("value_label", "Q1", "2.0")]["before"] == "Женщина"
    assert rows[("value_label", "Q2", "10.0")]["before"] == ""
    assert rows[("question_type", "Q2", "")] | {"id": None} == {
        "kind": "question_type", "question": "Q2", "before": "scale", "after": "numeric", "id": None
    }
    assert rows[("variable_label", "Q3_1", "")]["after"] == "Скорость обслуживания"
    order = next(row for row in result["rows"] if row["kind"] == "order")
    assert order["after"] == ["Q2", "Q1"]
    skipped = " ".join(result["skipped"])
    assert "Q9" in skipped and "NOPE" in skipped and "кода 5" in skipped
    assert "Q3: тип" in skipped
    assert result["notes"] == "Q4 не нашёл."


def test_chosen_rows_apply_as_one_undo_step(project) -> None:
    job = _parsed(project)
    rows = job["result"]["rows"]
    chosen = [row["id"] for row in rows if row["kind"] != "question_type"]
    response = project["client"].post(
        f"{project['base']}/questionnaire/apply", json={"job_id": job["job_id"], "row_ids": chosen}
    )
    assert response.status_code == 200, response.text
    applied = response.json()
    assert _question(applied, "Q2")["label"] == "Насколько вероятно, что вы порекомендуете нас?"
    assert _question(applied, "Q2")["question_type"] == "scale"
    labels = {
        value_key(item["value"]): item["label"]
        for item in _variable(applied, "Q1")["value_labels"]
    }
    assert labels == {"1": "Мужчина", "2": "Женщина, по анкете"}
    assert _variable(applied, "Q2")["value_labels"] == [
        {"value": 10.0, "label": "Точно порекомендую"}
    ]
    q3 = _question(applied, "Q3")
    assert q3["items"][0]["label"] == "Скорость обслуживания"
    codes = [item["code"] for item in applied["configuration"]["questions"]]
    assert codes.index("Q2") < codes.index("Q1")

    history = project["client"].get(f"{project['base']}/history").json()
    assert history["undo"] == 1
    project["client"].post(f"{project['base']}/undo")
    restored = _project(project)
    assert _question(restored, "Q2")["label"] == "Оцените сервис от 0 до 10"
    assert _variable(restored, "Q2")["value_labels"] == []
    assert "label_overrides" not in restored["configuration"]


def test_labels_survive_structure_refresh(project) -> None:
    job = _parsed(project)
    project["client"].post(
        f"{project['base']}/questionnaire/apply",
        json={"job_id": job["job_id"], "row_ids": [row["id"] for row in job["result"]["rows"]]},
    )
    refreshed = project["client"].post(f"{project['base']}/structure/refresh").json()
    assert _variable(refreshed, "Q3_1")["label"] == "Скорость обслуживания"
    assert _question(refreshed, "Q3")["items"][0]["label"] == "Скорость обслуживания"
    assert _variable(refreshed, "Q2")["value_labels"][0]["label"] == "Точно порекомендую"


def test_type_change_goes_through_question_validation(project) -> None:
    job = _parsed(project)
    row = next(row for row in job["result"]["rows"] if row["kind"] == "question_type")
    response = project["client"].post(
        f"{project['base']}/questionnaire/apply",
        json={"job_id": job["job_id"], "row_ids": [row["id"]]},
    )
    assert response.status_code == 200, response.text
    assert _question(response.json(), "Q2")["question_type"] == "numeric"


def test_failed_job_is_retried_with_same_input(project) -> None:
    project["model"] = ScriptedModel([ModelError("таймаут"), _submit(MAPPING)])
    job_id = _upload(project).json()["job_id"]
    failed = _wait(project, job_id)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "AI_PROVIDER_ERROR"
    assert "таймаут" in failed["error"]
    project["client"].post(f"{project['base']}/jobs/{job_id}/retry")
    done = _wait(project, job_id)
    assert done["status"] == "complete" and done["attempts"] == 2
    listed = project["client"].get(f"{project['base']}/jobs").json()["jobs"]
    assert listed[0]["job_id"] == job_id and listed[0]["result"] is None


def test_model_answering_in_text_is_asked_once_more(project) -> None:
    project["model"] = ScriptedModel([ModelReply(content="Вот сопоставление: …"), _submit(MAPPING)])
    job = _wait(project, _upload(project).json()["job_id"])
    assert job["status"] == "complete"
    assert "submit_mapping" in project["model"].requests[1][1][-1]["content"]


def test_apply_unknown_job_is_404(project) -> None:
    response = project["client"].post(
        f"{project['base']}/questionnaire/apply",
        json={"job_id": "00000000-0000-0000-0000-000000000000", "row_ids": ["r1"]},
    )
    assert response.status_code == 404


GROUPS_MAPPING = {
    "questions": [],
    "variables": [],
    "groups": [
        {"question_type": "multiple_choice_dichotomy", "codes": ["Q3_1", "Q3_2"],
         "label": "Что вам понравилось?"},
        # Пол и оценка — разные шкалы: матрицу из них не собрать.
        {"question_type": "matrix", "codes": ["Q1", "Q2"]},
        {"question_type": "ranking", "codes": ["Q1", "Q2"]},
        # Q3_1 уже ушёл в первую группу.
        {"question_type": "multiple_choice_dichotomy", "codes": ["Q3_1", "Q2"]},
    ],
    "order": ["Q3_1", "Q3_2", "Q1", "Q2"],
}


def _ungroup_q3(context: dict) -> None:
    response = context["client"].post(f"{context['base']}/questions/Q3/ungroup")
    assert response.status_code == 200, response.text


def test_questionnaire_groups_single_questions_in_one_undo_step(project) -> None:
    _ungroup_q3(project)
    job = _parsed(project, GROUPS_MAPPING)
    rows = job["result"]["rows"]
    groups = [row for row in rows if row["kind"] == "group"]
    assert len(groups) == 1
    assert groups[0]["codes"] == ["Q3_1", "Q3_2"]
    assert groups[0]["after"] == "Что вам понравилось?"
    skipped = " ".join(job["result"]["skipped"])
    assert "Матрица" in skipped and "ranking" in skipped and "другой предложенной группе" in skipped
    # Модель перечислила в порядке прежние коды пунктов — проверка их пропускает.
    assert next(row for row in rows if row["kind"] == "order")["after"] == [
        "Q3_1", "Q3_2", "Q1", "Q2",
    ]

    response = project["client"].post(
        f"{project['base']}/questionnaire/apply",
        json={"job_id": job["job_id"], "row_ids": [row["id"] for row in rows]},
    )
    assert response.status_code == 200, response.text
    applied = response.json()
    group = _question(applied, groups[0]["code"])
    assert group["question_type"] == "multiple_choice_dichotomy"
    assert group["source_variables"] == ["Q3_1", "Q3_2"]
    assert group["label"] == "Что вам понравилось?"
    codes = [item["code"] for item in applied["configuration"]["questions"]]
    assert "Q3_1" not in codes and "Q3_2" not in codes
    # Группа встала по анкете на место своих пунктов — перед Q1.
    assert codes.index(group["code"]) < codes.index("Q1") < codes.index("Q2")

    assert project["client"].get(f"{project['base']}/history").json()["undo"] == 2
    project["client"].post(f"{project['base']}/undo")
    restored = [item["code"] for item in _project(project)["configuration"]["questions"]]
    assert "Q3_1" in restored and group["code"] not in restored


def test_questionnaire_group_skips_questions_used_in_banner(project) -> None:
    banner = project["client"].post(
        f"{project['base']}/banners",
        json={"name": "Пол", "blocks": [{"sources": [{"kind": "question", "ref": "Q1"}]}]},
    )
    assert banner.status_code in {200, 201}, banner.text
    mapping = {
        "questions": [],
        "variables": [],
        "groups": [{"question_type": "multiple_choice_dichotomy", "codes": ["Q1", "Q2"]}],
    }
    job = _parsed(project, mapping)
    assert not [row for row in job["result"]["rows"] if row["kind"] == "group"]
    assert any("ссылаются" in item for item in job["result"]["skipped"])


def test_uploaded_questionnaire_text_is_kept_for_the_ai_report(project) -> None:
    from sav_analytics.core.questionnaire import stored_questionnaire

    _parsed(project)
    stored = stored_questionnaire(project["repository"].root / project["project_id"])
    assert stored["filename"] == "anketa.txt"
    assert "Ваш пол?" in stored["text"]
