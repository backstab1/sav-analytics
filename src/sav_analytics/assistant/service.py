"""Ассистент проекта: разговор с моделью и судьба её планов.

Журнал — `assistant.json` рядом с проектом, а не в `project.json`: тот
хэшируется в ключ кэша отчёта (та же причина, что у `history.json` в
решении 023). В журнале разговор в формате `models.py` и планы. План
хранит снимок того, что изменил, поэтому откатывается и тогда, когда уже
вытеснен из двадцати шагов общей отмены.

Модель вызывает только инструменты чтения и `propose_plan`. Применение,
отказ и откат приходят из интерфейса и модели не нужны; она узнаёт о них
событием перед следующим сообщением пользователя.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from ..atomic_file import replace_file
from . import plans
from .catalog import READ_TOOLS, Catalog, ToolInputError, table_report
from .changes import RevertConflict, changed_sections, project_changes, reverted_project
from .models import ChatModel, ModelReply, ToolSpec

JOURNAL_FILE = "assistant.json"
# Сколько сообщений разговора видит модель и сколько хранится.
_CONTEXT_MESSAGES = 40
_STORED_MESSAGES = 400
_MAX_ROUNDS = 10
_PROMPT = (Path(__file__).with_name("system_prompt.md")).read_text(encoding="utf-8")

_locks: dict[str, Lock] = {}
_locks_guard = Lock()

PROPOSE_TOOL = ToolSpec(
    "propose_plan",
    "Предложить изменение проекта. Ничего не меняет: пользователь увидит описание "
    "шагов и сам нажмёт «Применить». Шаги и их параметры перечислены в системных "
    "инструкциях, раздел «Шаги плана».",
    {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Одна-две фразы от первого лица: что и зачем будет сделано.",
            },
            "steps": {
                "type": "array",
                "description": "Шаги по порядку, от 1 до 20.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": plans.OPERATIONS},
                        "table": {"type": "string", "description": "id таблицы или $step:N"},
                        "rows": {"type": "array", "items": {"type": "string"}},
                        "codes": {"type": "array", "items": {"type": "string"}},
                        "cols": {
                            "type": "array",
                            "description": "Блоки колонок; в блоке одна или две переменные.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sources": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 2,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "kind": {
                                                    "type": "string",
                                                    "enum": ["question", "recoding"],
                                                },
                                                "ref": {"type": "string"},
                                            },
                                            "required": ["kind", "ref"],
                                        },
                                    },
                                },
                                "required": ["sources"],
                            },
                        },
                        "banner_id": {"type": "string"},
                        "filter_id": {"type": "string"},
                        "base": {"type": "string", "enum": ["main", "filter"]},
                        "measure": {"type": "string", "enum": ["value", "counts", "index"]},
                        "scale_box": {"type": "integer", "minimum": 1, "maximum": 3},
                        "code": {"type": "string"},
                        "nets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "values": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["label", "values"],
                            },
                        },
                        "name": {"type": "string"},
                        "label": {"type": "string"},
                        "expression": {"type": "string"},
                        "definition": {
                            "type": "object",
                            "description": "Определение перекодировки, см. recoding.create.",
                        },
                        "rule": {
                            "type": "object",
                            "description": "Правило фильтра, см. filter.create.",
                        },
                        "blocks": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Блоки баннера, см. banner.create.",
                        },
                    },
                    "required": ["op"],
                },
            },
        },
        "required": ["summary", "steps"],
    },
)
TOOLS = [*READ_TOOLS, PROPOSE_TOOL]


class AssistantError(ValueError):
    """Действие с планом нельзя выполнить: текст — для пользователя."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event_text(kind: str, plan: dict) -> str:
    return f"[{kind}] «{plan['summary']}»"


class AssistantService:
    def __init__(self, repository: Any, project_id: UUID) -> None:
        self.repository = repository
        self.project_id = project_id
        self.project_dir = repository.root / str(project_id)
        self.key = str(self.project_dir)

    # --- журнал -------------------------------------------------------------------

    def _lock(self) -> Lock:
        with _locks_guard:
            return _locks.setdefault(self.key, Lock())

    def _load(self) -> dict:
        path = self.project_dir / JOURNAL_FILE
        if not path.is_file():
            return {"conversation": [], "plans": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, journal: dict) -> None:
        journal["conversation"] = journal["conversation"][-_STORED_MESSAGES:]
        temporary = self.project_dir / f".{JOURNAL_FILE}.tmp"
        temporary.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file(temporary, self.project_dir / JOURNAL_FILE)

    def _plan(self, journal: dict, plan_id: str) -> dict:
        plan = next((item for item in journal["plans"] if item["id"] == plan_id), None)
        if plan is None:
            raise LookupError(plan_id)
        return plan

    # --- состояние для интерфейса --------------------------------------------------

    def state(self) -> dict:
        self.repository.get(self.project_id)
        journal = self._load()
        messages = []
        for message in journal["conversation"]:
            if message["role"] == "user":
                messages.append({"role": "user", "text": message["content"]})
            elif message["role"] == "event":
                messages.append({"role": "event", "text": message["display"]})
            elif message["role"] == "assistant" and (message.get("content") or "").strip():
                entry = {"role": "assistant", "text": message["content"]}
                if message.get("plan_id"):
                    entry["plan_id"] = message["plan_id"]
                messages.append(entry)
        return {"messages": messages, "plans": [_public(plan) for plan in journal["plans"]]}

    def reset(self) -> dict:
        """Начать разговор заново. Планы остаются: их можно откатить и потом."""
        with self._lock():
            journal = self._load()
            for plan in journal["plans"]:
                if plan["status"] == "pending":
                    plan["status"] = "superseded"
            journal["conversation"] = []
            self._save(journal)
        return self.state()

    # --- разговор ------------------------------------------------------------------

    def send(
        self, text: str, table_id: str | None, model: ChatModel, max_tool_calls: int
    ) -> dict:
        project = self.repository.get(self.project_id)
        table = table_report(project, table_id)
        system = _PROMPT.replace("{{project}}", project["name"]).replace(
            "{{table}}", f"«{table['name']}» (id {table['id']})" if table else "нет"
        )
        catalog = Catalog(self.repository, self.project_id, table["id"] if table else None)
        with self._lock():
            journal = self._load()
            conversation = journal["conversation"]
            conversation.append({"role": "user", "content": text, "at": _now()})
            view = _model_view(conversation)
            calls = 0
            proposed: dict | None = None
            final: dict | None = None
            for _ in range(_MAX_ROUNDS):
                reply: ModelReply = model.complete(system, view, TOOLS)
                message: dict[str, Any] = {
                    "role": "assistant",
                    "content": reply.content,
                    "tool_calls": [asdict(call) for call in reply.tool_calls],
                    "extra": reply.extra,
                }
                for call in message["tool_calls"]:
                    call.pop("raw_arguments", None)
                conversation.append(message)
                view.append(message)
                if not reply.tool_calls:
                    final = message
                    break
                for call in reply.tool_calls:
                    calls += 1
                    if calls > max_tool_calls:
                        result: dict = {"error": "Лимит вызовов инструментов за ход исчерпан."}
                    elif call.raw_arguments is not None:
                        result = {"error": "Аргументы инструмента — не JSON-объект."}
                    else:
                        result = self._call(
                            catalog, journal, call.name, call.arguments, table_id=catalog.table_id
                        )
                        if call.name == "propose_plan" and "plan_id" in result:
                            proposed = self._plan(journal, result["plan_id"])
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                    conversation.append(tool_message)
                    view.append(tool_message)
                if calls > max_tool_calls:
                    break
            if final is None:
                final = {
                    "role": "assistant",
                    "content": "Не удалось разобрать запрос до конца. Попробуйте сформулировать "
                    "его короче или разбить на части.",
                    "tool_calls": [],
                }
                conversation.append(final)
            if proposed is not None:
                final["plan_id"] = proposed["id"]
            self._save(journal)
        return {
            "reply": final["content"] or "",
            "plan": _public(proposed) if proposed else None,
        }

    def _call(
        self, catalog: Catalog, journal: dict, name: str, arguments: dict, *, table_id: str | None
    ) -> dict:
        try:
            if name == "propose_plan":
                return self._propose(journal, arguments, table_id)
            if name not in {tool.name for tool in READ_TOOLS}:
                return {"error": f"Инструмента {name} нет."}
            return getattr(catalog, name)(**arguments)
        except TypeError:
            return {"error": f"Неверные аргументы {name}: сверьтесь со схемой инструмента."}
        except (ToolInputError, plans.PlanError) as exc:
            return {"error": str(exc)}

    def _propose(self, journal: dict, arguments: dict, table_id: str | None) -> dict:
        summary = str(arguments.get("summary") or "").strip()
        steps = arguments.get("steps")
        if not summary:
            raise ToolInputError("Нужна summary: что и зачем будет сделано.")
        if not isinstance(steps, list) or not all(isinstance(item, dict) for item in steps):
            raise ToolInputError("steps — список объектов с полем op.")
        outcome = plans.dry_run(self.repository, self.project_id, table_id, steps)
        for plan in journal["plans"]:
            if plan["status"] == "pending":
                plan["status"] = "superseded"
        plan = {
            "id": str(uuid4()),
            "status": "pending",
            "created_at": _now(),
            "summary": summary[:1000],
            "table_id": table_id,
            "steps": steps,
            "description": outcome.description,
            "warnings": outcome.warnings,
        }
        journal["plans"].append(plan)
        return {
            "plan_id": plan["id"],
            "status": "pending",
            "description": outcome.description,
            "warnings": outcome.warnings,
        }

    # --- судьба плана ---------------------------------------------------------------

    def apply(self, plan_id: str) -> tuple[dict, dict]:
        with self._lock():
            journal = self._load()
            plan = self._plan(journal, plan_id)
            if plan["status"] != "pending":
                raise AssistantError("План уже не ждёт применения.")
            try:
                before, after, outcome = plans.apply(
                    self.repository,
                    self.project_id,
                    plan["table_id"],
                    plan["steps"],
                    key=f"assistant:{plan['id']}",
                )
            except plans.PlanError as exc:
                plan["status"] = "failed"
                plan["error"] = str(exc)
                self._save(journal)
                raise AssistantError(
                    f"План больше не подходит к проекту: {exc}. Попросите ассистента заново."
                ) from exc
            plan.update(
                status="applied",
                applied_at=_now(),
                description=outcome.description,
                warnings=outcome.warnings,
                changes=project_changes(before, after),
            )
            self._event(journal, "plan_applied", plan, "Применено")
            self._save(journal)
        return after, _public(plan)

    def decline(self, plan_id: str) -> dict:
        with self._lock():
            journal = self._load()
            plan = self._plan(journal, plan_id)
            if plan["status"] != "pending":
                raise AssistantError("План уже не ждёт решения.")
            plan["status"] = "declined"
            self._event(journal, "plan_declined", plan, "Отказались")
            self._save(journal)
        return _public(plan)

    def revert(self, plan_id: str, *, cascade: bool = False) -> tuple[dict, dict]:
        """Откатить применённый план без участия модели.

        Если его шаг — последний в истории отмены, это обычная отмена.
        Иначе возвращаются только объекты, изменённые планом, и только если
        с тех пор их не трогали. `cascade` отменяет план вместе со всеми
        шагами после него — пока он ещё в истории отмены.
        """
        with self._lock():
            journal = self._load()
            plan = self._plan(journal, plan_id)
            if plan["status"] != "applied":
                raise AssistantError("Откатить можно только применённый план.")
            key = f"assistant:{plan['id']}"
            undo = self.repository.history_stacks(self.project_id)["undo"]
            if undo and undo[-1].get("coalesce") == key:
                project = self.repository.undo(self.project_id)
            else:
                current = self.repository.get(self.project_id)
                try:
                    project = reverted_project(current, plan["changes"])
                    self.repository.save_project(self.project_id, project)
                    project = self.repository.get(self.project_id)
                except RevertConflict as exc:
                    if not cascade:
                        raise
                    depth = next(
                        (
                            len(undo) - index
                            for index, step in enumerate(undo)
                            if step.get("coalesce") == key
                        ),
                        None,
                    )
                    if depth is None:
                        raise AssistantError(
                            "План уже вытеснен из истории отмены, откатить его вместе с "
                            "последующими правками нельзя."
                        ) from exc
                    for _ in range(depth):
                        project = self.repository.undo(self.project_id)
            plan.update(status="reverted", reverted_at=_now())
            self._event(journal, "plan_reverted", plan, "Откачено")
            self._save(journal)
        return project, _public(plan)

    @staticmethod
    def _event(journal: dict, kind: str, plan: dict, display: str) -> None:
        journal["conversation"].append(
            {
                "role": "event",
                "content": _event_text(kind, plan),
                "display": f"{display}: {plan['summary']}",
                "plan_id": plan["id"],
                "at": _now(),
            }
        )


def _public(plan: dict) -> dict:
    """План для интерфейса: без снимков изменений."""
    result = {key: value for key, value in plan.items() if key not in {"changes", "steps"}}
    if plan.get("changes"):
        result["sections"] = changed_sections(plan["changes"])
    return result


def _model_view(conversation: list[dict]) -> list[dict]:
    """Хвост разговора для модели: события вливаются в следующее сообщение
    пользователя, начало всегда — сообщение пользователя."""
    view: list[dict] = []
    events: list[str] = []
    for message in conversation:
        if message["role"] == "event":
            events.append(message["content"])
            continue
        if message["role"] == "user":
            content = "\n".join([*events, message["content"]]) if events else message["content"]
            events = []
            view.append({"role": "user", "content": content})
            continue
        view.append({key: value for key, value in message.items() if key not in {"at", "plan_id"}})
    # Хвост режется только по границе хода: вызов инструмента без своего
    # ответа или ответ без вызова провайдер не примет.
    start = max(0, len(view) - _CONTEXT_MESSAGES)
    while start > 0 and view[start]["role"] != "user":
        start -= 1
    return view[start:]
