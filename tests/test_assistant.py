"""Ассистент «Таблиц»: модель только предлагает, бэкенд применяет и откатывает.

Модель подменена сценарием: провайдер к проверке не относится, а поведение
ядра — проверка плана, описание, одна ревизия на план, выборочный откат —
от модели не зависит.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.api_dependencies import get_chat_model
from sav_analytics.assistant.models import ModelReply, OpenAICompatibleModel, ToolCall, ToolSpec
from sav_analytics.assistant.service import _model_view
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture

RECODING_PLAN = {
    "summary": "Сделаю кросс Q1 по группам оценки Q2.",
    "steps": [
        {
            "op": "recoding.create",
            "definition": {
                "mode": "ranges",
                "code": "SCORE_GROUP",
                "name": "Группы оценки",
                "source_variable": "Q2",
                "categories": [
                    {"label": "Низкая", "lower": 0, "upper": 7},
                    {"label": "Высокая", "lower": 8, "upper": 10},
                ],
            },
        },
        {"op": "table.set_rows", "rows": ["Q1"]},
        {"op": "table.set_columns", "cols": [{"kind": "recoding", "ref": "SCORE_GROUP"}]},
    ],
}


class ScriptedModel:
    def __init__(self, replies: list[ModelReply]) -> None:
        self.replies = list(replies)
        self.requests: list[tuple[str, list[dict], list[ToolSpec]]] = []

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> ModelReply:
        self.requests.append((system, [dict(item) for item in messages], tools))
        return self.replies.pop(0)


def _call(name: str, arguments: dict, identifier: str = "call-1") -> ModelReply:
    return ModelReply(content=None, tool_calls=[ToolCall(identifier, name, arguments)])


def _text(content: str) -> ModelReply:
    return ModelReply(content=content)


@pytest.fixture
def project(tmp_path: Path) -> Iterator[dict]:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    context: dict = {"repository": repository, "model": None}
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_chat_model] = lambda: context["model"]
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects",
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()["id"]
            table = client.post(
                f"/api/projects/{project_id}/tables/reports", json={"rows": ["Q1"]}
            ).json()["configuration"]["table_reports"][0]
            context.update(client=client, project_id=project_id, table_id=table["id"])
            context["base"] = f"/api/projects/{project_id}/assistant"
            yield context
    finally:
        app.dependency_overrides.pop(get_repository, None)
        app.dependency_overrides.pop(get_chat_model, None)


def _project(context: dict) -> dict:
    return context["client"].get(f"/api/projects/{context['project_id']}").json()


def _ask(context: dict, replies: list[ModelReply], text: str = "Сделай кросс") -> dict:
    context["model"] = ScriptedModel(replies)
    response = context["client"].post(
        f"{context['base']}/messages", json={"text": text, "table_id": context["table_id"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _propose(context: dict, plan: dict = RECODING_PLAN) -> dict:
    result = _ask(
        context,
        [
            _call("get_current_table", {}),
            _call("propose_plan", plan, "call-2"),
            _text("Проверьте план и нажмите «Применить»."),
        ],
    )
    assert result["plan"] is not None, result
    return result["plan"]


def test_disabled_assistant_answers_503_and_says_so(project) -> None:
    client = project["client"]
    assert client.get(project["base"]).json()["enabled"] is False
    response = client.post(f"{project['base']}/messages", json={"text": "привет"})
    assert response.status_code == 503
    assert response.json()["error_code"] == "ASSISTANT_DISABLED"


def test_read_tools_give_metadata_and_column_rules(project) -> None:
    _ask(
        project,
        [
            _call("list_questions", {}),
            _call("get_question", {"code": "Q1"}, "call-2"),
            _text("Нашёл."),
        ],
    )
    requests = project["model"].requests
    questions = json.loads(requests[1][1][-1]["content"])["questions"]
    by_code = {item["code"]: item for item in questions}
    assert by_code["Q1"]["can_be_column"] is True
    assert by_code["Q2"]["can_be_column"] is False
    details = json.loads(requests[2][1][-1]["content"])
    assert [item["label"] for item in details["categories"]] == ["Мужчина", "Женщина"]
    # В системном промпте — проект и открытая таблица, инструменты — чтение и план.
    system, _, tools = requests[0]
    assert "research" in system and project["table_id"] in system
    assert {tool.name for tool in tools} >= {"get_current_table", "propose_plan"}


def test_proposal_changes_nothing_and_describes_steps_itself(project) -> None:
    revision = _project(project)["configuration"]["revision"]
    plan = _propose(project)
    assert plan["status"] == "pending"
    assert plan["description"][0].startswith("Создам перекодировку SCORE_GROUP «Группы оценки»")
    assert "колонки — SCORE_GROUP «Группы оценки»" in plan["description"][2]
    configuration = _project(project)["configuration"]
    assert configuration["revision"] == revision
    assert configuration["recodings"] == []

    state = project["client"].get(project["base"]).json()
    assert state["messages"][-1] == {
        "role": "assistant",
        "text": "Проверьте план и нажмите «Применить».",
        "plan_id": plan["id"],
    }


def test_invalid_plan_goes_back_to_the_model_as_an_error(project) -> None:
    result = _ask(
        project,
        [
            _call("propose_plan", {"summary": "Кросс", "steps": [
                {"op": "table.set_columns", "cols": [{"kind": "question", "ref": "Q2"}]}
            ]}),
            _text("Q2 числовой, сначала нужна перекодировка. Сделать 2 группы?"),
        ],
    )
    assert result["plan"] is None
    error = json.loads(project["model"].requests[1][1][-1]["content"])["error"]
    assert "Шаг 1 (table.set_columns)" in error and "перекодировка" in error


def test_apply_writes_one_revision_and_undo_restores_everything(project) -> None:
    client, base = project["client"], project["base"]
    revision = _project(project)["configuration"]["revision"]
    plan = _propose(project)

    applied = client.post(f"{base}/plans/{plan['id']}/apply")
    assert applied.status_code == 200, applied.text
    configuration = applied.json()["project"]["configuration"]
    assert configuration["revision"] == revision + 1
    recoding = configuration["recodings"][0]
    table = configuration["table_reports"][0]
    assert table["cols"] == [{"kind": "recoding", "ref": recoding["id"]}]
    assert applied.json()["plan"]["status"] == "applied"
    assert applied.json()["plan"]["sections"] == ["перекодировки", "таблицы"]

    # Повторно применить нельзя, а модель узнаёт о применении событием.
    assert client.post(f"{base}/plans/{plan['id']}/apply").status_code == 422
    _ask(project, [_text("Готово.")], text="Что дальше?")
    last_user = project["model"].requests[0][1][-1]
    assert last_user["content"].startswith("[plan_applied]")

    reverted = client.post(f"{base}/plans/{plan['id']}/revert")
    assert reverted.status_code == 200, reverted.text
    configuration = reverted.json()["project"]["configuration"]
    assert configuration["recodings"] == []
    assert configuration["table_reports"][0]["cols"] == []
    assert reverted.json()["plan"]["status"] == "reverted"


def test_revert_keeps_later_unrelated_edits(project) -> None:
    client, base = project["client"], project["base"]
    plan = _propose(project)
    client.post(f"{base}/plans/{plan['id']}/apply")
    filters = client.post(
        f"/api/projects/{project['project_id']}/filters",
        json={"name": "Мужчины", "rule": {"operator": "and", "items": [
            {"source": {"kind": "question", "ref": "Q1"}, "operator": "eq", "values": [1]}
        ]}},
    )
    assert filters.status_code == 201, filters.text

    reverted = client.post(f"{base}/plans/{plan['id']}/revert")
    assert reverted.status_code == 200, reverted.text
    configuration = reverted.json()["project"]["configuration"]
    assert configuration["recodings"] == []
    assert [item["name"] for item in configuration["filters"]] == ["Мужчины"]


def test_revert_conflict_is_named_and_cascade_undoes_later_edits(project) -> None:
    client, base = project["client"], project["base"]
    plan = _propose(project)
    client.post(f"{base}/plans/{plan['id']}/apply")
    table_url = f"/api/projects/{project['project_id']}/tables/reports/{project['table_id']}"
    table = _project(project)["configuration"]["table_reports"][0]
    changed = client.put(table_url, json={**{k: table[k] for k in ("cols", "rows")},
                                          "measure": "index"})
    assert changed.status_code == 200, changed.text

    conflict = client.post(f"{base}/plans/{plan['id']}/revert")
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "ASSISTANT_REVERT_CONFLICT"
    assert "таблицы" in conflict.json()["detail"]

    cascade = client.post(f"{base}/plans/{plan['id']}/revert", json={"cascade": True})
    assert cascade.status_code == 200, cascade.text
    configuration = cascade.json()["project"]["configuration"]
    assert configuration["recodings"] == []
    assert configuration["table_reports"][0]["measure"] == "value"


def test_declined_plan_cannot_be_applied_and_new_proposal_supersedes(project) -> None:
    client, base = project["client"], project["base"]
    first = _propose(project)
    second = _propose(project)
    plans = {item["id"]: item for item in client.get(base).json()["plans"]}
    assert plans[first["id"]]["status"] == "superseded"
    assert client.post(f"{base}/plans/{second['id']}/decline").json()["plan"]["status"] == (
        "declined"
    )
    assert client.post(f"{base}/plans/{second['id']}/apply").status_code == 422


def test_saved_banner_does_not_become_the_report_banner(project) -> None:
    client, base = project["client"], project["base"]
    before = _project(project)["configuration"]["report_banner_id"]
    plan = _propose(project, {
        "summary": "Сохраню разрез по полу баннером и поставлю его в таблицу.",
        "steps": [
            {"op": "banner.create", "name": "Пол", "blocks": [
                {"sources": [{"kind": "question", "ref": "Q1"}]}
            ]},
            {"op": "table.use_banner", "banner_id": "$step:1"},
            {"op": "table.set_nets", "code": "Q1", "nets": [
                {"label": "Все", "values": ["1", "2"]}
            ]},
        ],
    })
    assert plan["description"][0].startswith("Сохраню баннер «Пол»")
    configuration = client.post(f"{base}/plans/{plan['id']}/apply").json()["project"][
        "configuration"
    ]
    banner = configuration["banners"][0]
    table = configuration["table_reports"][0]
    assert configuration["report_banner_id"] == before
    assert table["banner_id"] == banner["id"]
    assert table["nets"] == {"Q1": [{"label": "Все", "values": [1.0, 2.0]}]}


def test_draft_keeps_writes_in_memory(project) -> None:
    repository: ProjectRepository = project["repository"]
    from uuid import UUID

    identifier = UUID(project["project_id"])
    revision = repository.get(identifier)["configuration"]["revision"]
    with repository.draft(identifier):
        repository.rename_table_report(identifier, UUID(project["table_id"]), "Черновик")
        assert repository.get(identifier)["configuration"]["table_reports"][0]["name"] == (
            "Черновик"
        )
    stored = repository.get(identifier)["configuration"]
    assert stored["revision"] == revision
    assert stored["table_reports"][0]["name"] == "Таблица 1"


def test_model_view_folds_events_into_the_next_user_message() -> None:
    conversation = [
        {"role": "user", "content": "раз"},
        {"role": "assistant", "content": "ок", "tool_calls": []},
        {"role": "event", "content": "[plan_applied] «x»", "display": "Применено: x"},
        {"role": "user", "content": "два"},
    ]
    view = _model_view(conversation)
    assert [item["role"] for item in view] == ["user", "assistant", "user"]
    assert view[-1]["content"] == "[plan_applied] «x»\nдва"


def test_openai_compatible_adapter_speaks_the_wire_format() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "auth": request.headers.get("authorization"),
                     "body": json.loads(request.content)})
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant",
            "content": None,
            "reasoning_content": "думаю",
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "get_question", "arguments": "{\"code\": \"Q1\"}"}}],
        }}]})

    model = OpenAICompatibleModel(
        base_url="https://provider.test/v1/", api_key="secret", model="some-model",
        transport=httpx.MockTransport(handler),
    )
    tool = ToolSpec("get_question", "Вопрос", {"type": "object", "properties": {}})
    reply = model.complete("система", [{"role": "user", "content": "привет"}], [tool])
    assert reply.tool_calls[0].arguments == {"code": "Q1"}
    assert reply.extra == {"reasoning_content": "думаю"}

    history = [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": None, "extra": reply.extra, "tool_calls": [
            {"id": "c1", "name": "get_question", "arguments": {"code": "Q1"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "get_question", "content": "{}"},
    ]
    model.complete("система", history, [tool])
    first, second = seen
    assert first["url"] == "https://provider.test/v1/chat/completions"
    assert first["auth"] == "Bearer secret"
    assert first["body"]["messages"][0] == {"role": "system", "content": "система"}
    assert first["body"]["tools"][0]["function"]["name"] == "get_question"
    assert first["body"]["tool_choice"] == "auto"
    echoed = second["body"]["messages"][2]
    assert echoed["reasoning_content"] == "думаю"
    assert echoed["tool_calls"][0]["function"]["arguments"] == "{\"code\": \"Q1\"}"
    assert second["body"]["messages"][3]["role"] == "tool"


def test_adapter_tells_empty_arguments_from_broken_ones() -> None:
    from sav_analytics.assistant.models import _parse_call

    empty = _parse_call({"id": "a", "function": {"name": "list_tables", "arguments": "{}"}})
    assert empty.arguments == {} and empty.raw_arguments is None
    broken = _parse_call({"id": "b", "function": {"name": "list_tables", "arguments": "{oops"}})
    assert broken.raw_arguments == "{oops"
