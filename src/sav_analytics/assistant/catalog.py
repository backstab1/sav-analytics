"""Инструменты чтения: что модель знает о проекте.

По решению 030 модели уходят только метаданные — коды, подписи, типы,
названия и правила объектов конфигурации. Строк массива, открытых ответов
и чисел таблиц здесь нет и быть не должно: новый инструмент, который вернул
бы их, требует нового решения. Исключение одно — `suggest_ranges`, он
отдаёт границы интервалов, а не значения.

Правила «можно в строки» и «можно в колонки» повторяют экран «Таблиц»
(`publishVariablesToShell` в `static/app.js`): таблица, собранная
ассистентом, должна открываться с теми же галочками.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ..core.multiple_response import MULTIPLE_TYPES
from ..core.recoding import RecodingError, suggest_ranges
from .models import ToolSpec

ROW_TYPES = frozenset(
    {
        "single_choice",
        "scale",
        "numeric",
        "multiple_choice_dichotomy",
        "multiple_choice_categorical",
        "matrix",
        "ranking",
    }
)
_LIST_LIMIT = 80


class ToolInputError(ValueError):
    """Модель вызвала инструмент с неверными аргументами: текст уходит ей."""


def value_labels(project: dict, question: dict) -> list[dict]:
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    sources = question.get("source_variables") or []
    if len(sources) > 1:
        return [
            {"value": name, "label": variables.get(name, {}).get("label") or name}
            for name in sources
        ]
    labels = variables.get(sources[0], {}).get("value_labels") if sources else None
    return [{"value": item["value"], "label": item["label"]} for item in labels or []]


def can_be_row(question: dict) -> bool:
    return bool(question["included_in_report"]) and question["question_type"] in ROW_TYPES


def can_be_column(project: dict, question: dict) -> bool:
    if not question["included_in_report"]:
        return False
    if question["question_type"] in MULTIPLE_TYPES:
        return True
    return (
        question["question_type"] == "single_choice"
        and len(question.get("source_variables") or []) == 1
        and bool(value_labels(project, question))
    )


def table_report(project: dict, table_id: str | None) -> dict | None:
    reports = project["configuration"].get("table_reports", [])
    return next((item for item in reports if item["id"] == table_id), None)


def question_by_code(project: dict, code: str) -> dict | None:
    return next(
        (item for item in project["configuration"]["questions"] if item["code"] == code), None
    )


def recoding_by_ref(project: dict, ref: str) -> dict | None:
    """Перекодировка по id или по коду: модели удобнее код, конфигурации — id."""
    recodings = project["configuration"]["recodings"]
    return next((item for item in recodings if item["id"] == ref), None) or next(
        (item for item in recodings if item["code"].casefold() == str(ref).casefold()), None
    )


def _question_summary(project: dict, question: dict) -> dict:
    return {
        "code": question["code"],
        "label": question["label"],
        "type": question["question_type"],
        "role": question["role"],
        "in_report": question["included_in_report"],
        "can_be_row": can_be_row(question),
        "can_be_column": can_be_column(project, question),
    }


def _recoding_summary(recoding: dict) -> dict:
    summary = {
        "id": recoding["id"],
        "code": recoding["code"],
        "name": recoding["name"],
        "mode": recoding.get("mode", "ranges"),
        "categories": [item.get("label") for item in recoding.get("categories", [])],
        "can_be_row": False,
        "can_be_column": True,
    }
    if recoding.get("source_variable"):
        summary["source"] = recoding["source_variable"]
    return summary


def _layout(report: dict) -> dict:
    return {
        key: report.get(key)
        for key in (
            "rows",
            "cols",
            "nested",
            "banner_id",
            "filter_id",
            "sheet",
            "measure",
            "scale_box",
            "nets",
        )
    }


class Catalog:
    """Инструменты чтения для одного проекта и открытой таблицы."""

    def __init__(self, repository: Any, project_id: UUID, table_id: str | None) -> None:
        self.repository = repository
        self.project_id = project_id
        self.table_id = table_id

    def project(self) -> dict:
        return self.repository.get(self.project_id)

    def get_current_table(self) -> dict:
        project = self.project()
        report = table_report(project, self.table_id)
        if report is None:
            return {"table": None, "note": "Таблица не открыта: план может создать новую."}
        return {"table": {"id": report["id"], "name": report["name"], **_layout(report)}}

    def list_tables(self) -> dict:
        reports = self.project()["configuration"].get("table_reports", [])
        return {
            "tables": [
                {"id": item["id"], "name": item["name"], "current": item["id"] == self.table_id}
                for item in reports
            ]
        }

    def list_questions(self, query: str | None = None, types: list[str] | None = None) -> dict:
        project = self.project()
        needle = (query or "").strip().casefold()
        found = []
        for question in project["configuration"]["questions"]:
            if types and question["question_type"] not in types:
                continue
            if needle and needle not in f"{question['code']} {question['label']}".casefold():
                continue
            found.append(_question_summary(project, question))
        return {
            "questions": found[:_LIST_LIMIT],
            "total": len(found),
            "truncated": len(found) > _LIST_LIMIT,
        }

    def get_question(self, code: str) -> dict:
        project = self.project()
        question = question_by_code(project, code)
        if question is None:
            raise ToolInputError(f"Вопроса с кодом {code} нет. Найдите код через list_questions.")
        details = _question_summary(project, question)
        details.update(
            {
                "categories": value_labels(project, question),
                "source_variables": question.get("source_variables") or [],
                "special_values": question.get("special_values") or [],
                "nets": question.get("nets") or [],
                "special_metric": question.get("special_metric") or "none",
            }
        )
        if question["question_type"] in MULTIPLE_TYPES:
            details["multiple_response"] = question.get("multiple_response")
        return details

    def list_recodings(self) -> dict:
        recodings = self.project()["configuration"]["recodings"]
        return {"recodings": [_recoding_summary(item) for item in recodings]}

    def list_filters(self) -> dict:
        filters = self.project()["configuration"]["filters"]
        return {
            "filters": [
                {"id": item["id"], "name": item["name"], "rule": item["rule"]} for item in filters
            ]
        }

    def list_banners(self) -> dict:
        banners = self.project()["configuration"]["banners"]
        return {
            "banners": [
                {"id": item["id"], "name": item["name"], "blocks": item.get("blocks", [])}
                for item in banners
            ]
        }

    def suggest_ranges(self, variable: str, method: str = "quantiles", groups: int = 4) -> dict:
        if method not in {"quantiles", "equal"}:
            raise ToolInputError("method — quantiles или equal.")
        if not 2 <= int(groups) <= 20:
            raise ToolInputError("groups — от 2 до 20.")
        project = self.project()
        source = next(
            (item for item in project["inspection"]["variables"] if item["name"] == variable),
            None,
        )
        if source is None:
            raise ToolInputError(f"Переменной {variable} нет в массиве.")
        try:
            return suggest_ranges(
                self.repository.source_path(self.project_id), source, method, int(groups), project
            )
        except RecodingError as exc:
            raise ToolInputError(str(exc)) from exc


READ_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "get_current_table",
        "Раскладка открытой таблицы: строки, колонки, вложенность, баннер, фильтр, база, "
        "показатель, топ-бокс, NET-ы.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        "list_tables",
        "Сохранённые таблицы проекта: id и название.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        "list_questions",
        "Вопросы анкеты: код, подпись, тип, можно ли в строки и в колонки. "
        "query ищет по коду и подписи без учёта регистра.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Часть кода или подписи."},
                "types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "single_choice",
                            "multiple_choice_dichotomy",
                            "multiple_choice_categorical",
                            "scale",
                            "numeric",
                            "ranking",
                            "matrix",
                            "open_text",
                            "technical",
                        ],
                    },
                },
            },
        },
    ),
    ToolSpec(
        "get_question",
        "Подробности вопроса: категории (значение и подпись), исходные переменные, "
        "спецзначения, NET-ы, настройки multiple-response.",
        {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    ),
    ToolSpec(
        "list_recodings",
        "Перекодировки проекта: id, код, название, режим, категории. В колонки идут по коду.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        "list_filters",
        "Сохранённые фильтры: id, название, правило.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        "list_banners",
        "Сохранённые баннеры: id, название, блоки колонок.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        "suggest_ranges",
        "Границы интервалов для перекодировки числовой переменной: quantiles — равные по "
        "численности группы, equal — равные по ширине.",
        {
            "type": "object",
            "properties": {
                "variable": {"type": "string", "description": "Имя переменной в массиве."},
                "method": {"type": "string", "enum": ["quantiles", "equal"]},
                "groups": {"type": "integer", "minimum": 2, "maximum": 20},
            },
            "required": ["variable"],
        },
    ),
]
