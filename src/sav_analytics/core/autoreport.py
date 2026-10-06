"""Автоотчёт (PQ.18, решение 034): бриф, план, числа ядра, текст модели.

Модель выбирает, что считать (план: разрез, вес, разделы и вопросы), и
пишет текст по готовым числам. Числа считает `build_live_table` — та же
функция, что пишет лист книги, поэтому процент в документе совпадает с
книгой и `statistics.txt`. Число в тексте модели, которого нет среди чисел
раздела, помечается предупреждением, а не молча остаётся в отчёте.

Состояние — бриф, план, уточнения, собранный отчёт с правками человека —
лежит в `autoreport.json` рядом с проектом, а не в `project.json`: оно не
входит в ключ кэша отчёта и в историю отмены, как журнал ассистента.
Настройки, которые план меняет в проекте (баннер «Автоотчёт», вес), пишутся
обычной ревизией и видны в «Отчётах» как ручные.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..atomic_file import replace_file
from .reporting.live import LIVE_QUESTION_TYPES

FILE = "autoreport.json"
# Те же типы, что принимает баннер ядра (core/banner.py).
BANNER_TYPES = {"single_choice", "multiple_choice_dichotomy", "multiple_choice_categorical"}
MAX_BANNER = 4
MAX_BANNER_CATEGORIES = 12
MAX_SECTIONS = 10
MAX_SECTION_QUESTIONS = 8
# Колонок разреза в таблице документа: шире таблица на странице не читается.
MAX_DOC_COLUMNS = 9


def empty_state() -> dict[str, Any]:
    return {
        "brief": {"tasks": "", "description": "", "object": ""},
        "plan": None,
        "clarifications": [],
        "answers": {},
        "report": None,
    }


def load(project_dir: Path) -> dict[str, Any]:
    path = project_dir / FILE
    if not path.is_file():
        return empty_state()
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_state()
    return {**empty_state(), **stored}


def save(project_dir: Path, state: dict[str, Any]) -> None:
    temporary = project_dir / f".{FILE}.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    replace_file(temporary, project_dir / FILE)


# ---------------------------------------------------------------- план


def _variables(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in project["inspection"]["variables"]}


def _categories(project: dict[str, Any], question: dict[str, Any]) -> list[str]:
    variables = _variables(project)
    sources = question.get("source_variables") or []
    if question["question_type"].startswith("multiple") or question["question_type"] in {
        "matrix", "ranking"
    }:
        return [variables.get(name, {}).get("label") or name for name in sources]
    labels = (variables.get(sources[0], {}) if sources else {}).get("value_labels") or []
    return [item["label"] for item in labels]


def plan_catalog(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Вопросы, которые можно поставить в отчёт: код, подпись, тип, варианты."""
    entries = []
    for question in project["configuration"]["questions"]:
        if question["question_type"] not in LIVE_QUESTION_TYPES or question["role"] != "question":
            continue
        categories = _categories(project, question)
        entries.append(
            {
                "code": question["code"],
                "label": question["label"][:300],
                "type": question["question_type"],
                "categories": [label[:80] for label in categories[:15]],
                "more_categories": max(0, len(categories) - 15),
                "open_coded": bool(question.get("codeframe_id")),
            }
        )
    return entries


def weight_candidates(project: dict[str, Any]) -> list[str]:
    return [
        item["source_variables"][0] for item in project["configuration"]["questions"]
        if item["role"] == "weight" and len(item.get("source_variables") or []) == 1
    ]


def validate_plan(
    project: dict[str, Any], proposal: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Ответ модели → план, уточняющие вопросы и отброшенное с причинами."""
    questions = {item["code"]: item for item in project["configuration"]["questions"]}
    warnings: list[str] = []

    banner = []
    for code in proposal.get("banner") or []:
        question = questions.get(str(code))
        if question is None:
            warnings.append(f"Разреза {code} нет в массиве.")
        elif question["question_type"] not in BANNER_TYPES:
            warnings.append(f"{code} не подходит для разреза: нужен вопрос с вариантами ответа.")
        elif len(_categories(project, question)) > MAX_BANNER_CATEGORIES:
            warnings.append(f"{code}: слишком много вариантов для разреза.")
        elif str(code) not in banner:
            banner.append(str(code))
    banner = banner[:MAX_BANNER]

    weight = str(proposal.get("weight") or "")
    if weight and weight not in weight_candidates(project):
        warnings.append(f"Вес {weight} не объявлен весом в структуре — отчёт без веса.")
        weight = ""

    sections = []
    for item in proposal.get("sections") or []:
        if not isinstance(item, dict):
            continue
        codes = []
        for code in item.get("questions") or []:
            question = questions.get(str(code))
            if question is None:
                warnings.append(f"Вопроса {code} нет в массиве.")
            elif question["question_type"] not in LIVE_QUESTION_TYPES:
                warnings.append(f"{code}: такой вопрос в отчёт не выводится.")
            elif str(code) not in codes:
                codes.append(str(code))
        title = " ".join(str(item.get("title") or "").split())[:200]
        if not codes or not title:
            continue
        sections.append(
            {
                "id": str(item.get("id") or uuid4()),
                "title": title,
                "goal": str(item.get("goal") or "")[:500],
                "questions": codes[:MAX_SECTION_QUESTIONS],
            }
        )
    sections = sections[:MAX_SECTIONS]

    clarifications = []
    for index, item in enumerate(proposal.get("clarifications") or []):
        if not isinstance(item, dict) or not str(item.get("question") or "").strip():
            continue
        clarifications.append(
            {
                "id": f"q{index + 1}",
                "question": str(item["question"]).strip()[:500],
                "options": [str(option)[:200] for option in item.get("options") or []][:6],
            }
        )
    plan = {
        "banner": banner,
        "weight": weight or None,
        "sections": sections,
        "notes": str(proposal.get("notes") or "")[:2000],
    }
    return plan, clarifications[:3], warnings


def checked_plan(project: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """План после правки человеком: та же проверка, id разделов сохраняются."""
    validated, _clarifications, warnings = validate_plan(project, plan)
    return validated, warnings


# ---------------------------------------------------------------- числа


def table_numbers(live: dict[str, Any]) -> list[dict[str, Any]]:
    """Живая таблица → компактные числа для модели и документа.

    Колонки — Total и группы разреза с базой; строки значений — доля и
    значимость отличия группы (выше/ниже).
    """
    columns = [
        {"label": column["label"], "base": column["base"], "small": column["small"]}
        for column in live["columns"]
    ]
    result = []
    for question in live["questions"]:
        rows = []
        for row in question["rows"]:
            if row["kind"] != "value":
                continue
            cells = [
                {
                    "value": None if cell.get("value") is None
                    else round(float(cell["value"]), cell.get("decimals", 0)),
                    "direction": cell.get("direction"),
                    "small": cell.get("small", False),
                }
                for cell in row["cells"]
            ]
            rows.append({"label": str(row["label"]), "cells": cells})
        result.append(
            {"code": question["code"], "label": question["label"], "columns": columns,
             "rows": rows}
        )
    return result


def numbers_for_model(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Те же числа короче: модели не нужны признаки малой базы по ячейкам."""
    compact = []
    for table in tables:
        labels = [column["label"] for column in table["columns"]]
        compact.append(
            {
                "code": table["code"],
                "question": table["label"],
                "columns": [
                    f"{label} (n={column['base']}{', малая база' if column['small'] else ''})"
                    for label, column in zip(labels, table["columns"], strict=True)
                ],
                "rows": [
                    {
                        "label": row["label"],
                        "values": [cell["value"] for cell in row["cells"]],
                        "significant": [
                            {"higher": "выше", "lower": "ниже"}.get(cell["direction"] or "", "")
                            for cell in row["cells"]
                        ],
                    }
                    for row in table["rows"]
                ],
            }
        )
    return compact


_NUMBER = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(%|п\.\s?п\.|процент)", re.IGNORECASE)


def unverified_numbers(text: str, tables: list[dict[str, Any]]) -> list[str]:
    """Проценты в тексте, которых нет среди чисел раздела и их разностей.

    Разность — «на 12 п.п. выше» — допустима внутри одной строки таблицы.
    Сверка с округлением до целого: модель вправе писать «около 45%».
    """
    allowed: set[int] = set()
    for table in tables:
        for row in table["rows"]:
            values = [cell["value"] for cell in row["cells"] if cell["value"] is not None]
            for value in values:
                allowed.update({int(value), round(value), int(value) + 1})
            for left in values:
                for right in values:
                    difference = abs(left - right)
                    allowed.update({int(difference), round(difference), int(difference) + 1})
    found = []
    for match in _NUMBER.finditer(text):
        number = float(match.group(1).replace(",", "."))
        if round(number) not in allowed and int(number) not in allowed:
            found.append(match.group(0))
    return found


def method_text(
    project: dict[str, Any], live: dict[str, Any] | None, plan: dict[str, Any]
) -> str:
    """Методология без модели: база, вес, тест и порог — из настроек расчёта."""
    if live is None:
        return ""
    settings = live["settings"]
    total = live["columns"][0]["base"] if live["columns"] else 0
    parts = [f"Выборка: {total:,} респондентов.".replace(",", " ")]
    if settings.get("weight"):
        parts.append(f"Данные взвешены ({settings['weight']}).")
    else:
        parts.append("Данные не взвешены.")
    level = round(settings["confidence_level"] * 100)
    target = "с остальной выборкой" if settings["compare_target"] == "rest" else "с итогом"
    parts.append(
        f"Значимость различий группы {target} проверена при уровне доверия {level}%"
        + (" с поправкой Бонферрони" if settings.get("bonferroni") else "")
        + "; ▲ и ▼ — значимо выше и ниже."
    )
    parts.append(
        f"Группы с базой меньше {settings['minimum_base']} отмечены как малые: "
        "выводы по ним предварительные."
    )
    if plan.get("banner"):
        labels = {item["code"]: item["label"] for item in project["configuration"]["questions"]}
        cuts = ", ".join(labels.get(code, code) for code in plan["banner"])
        parts.append(f"Разрез: {cuts}.")
    return " ".join(parts)


def assemble_report(
    previous: dict[str, Any] | None,
    plan: dict[str, Any],
    tables: dict[str, list[dict[str, Any]]],
    texts: dict[str, Any],
    method: str,
    revision: int,
    overwrite: set[str],
) -> dict[str, Any]:
    """Собрать отчёт; правленные человеком блоки остаются, если их не
    перечислили в `overwrite`. Ключ блока: id раздела, `summary`,
    `conclusion`."""
    kept = {}
    if previous:
        for section in previous.get("sections", []):
            kept[section["id"]] = section
        for key in ("summary", "conclusion"):
            kept[key] = previous.get(key)
    section_texts = {
        str(item.get("id")): str(item.get("text") or "").strip()
        for item in texts.get("sections") or [] if isinstance(item, dict)
    }

    def block(key: str, text: str) -> dict[str, Any]:
        old = kept.get(key)
        if old and old.get("edited") and key not in overwrite:
            return {"text": old["text"], "edited": True}
        return {"text": text, "edited": False}

    sections = []
    for section in plan["sections"]:
        section_tables = tables.get(section["id"], [])
        text_block = block(section["id"], section_texts.get(section["id"], ""))
        old = kept.get(section["id"]) or {}
        hidden_cards = {card["code"] for card in old.get("cards", []) if card.get("hidden")}
        chart_kinds = {card["code"]: card.get("chart", "bar") for card in old.get("cards", [])}
        sections.append(
            {
                "id": section["id"],
                "title": section["title"],
                "text": text_block["text"],
                "edited": text_block["edited"],
                "warnings": [] if text_block["edited"]
                else unverified_numbers(text_block["text"], section_tables),
                "cards": [
                    {
                        "code": table["code"],
                        "label": table["label"],
                        "chart": chart_kinds.get(table["code"], "bar"),
                        "hidden": table["code"] in hidden_cards,
                        "table": table,
                    }
                    for table in section_tables
                ],
            }
        )
    summary = [str(line).strip() for line in texts.get("summary") or [] if str(line).strip()]
    summary_block = block("summary", "\n".join(summary))
    conclusion_block = block("conclusion", str(texts.get("conclusion") or "").strip())
    all_tables = [table for section_tables in tables.values() for table in section_tables]
    return {
        "built_at": datetime.now(UTC).isoformat(),
        "revision": revision,
        "method": method,
        "summary": {
            **summary_block,
            "warnings": [] if summary_block["edited"]
            else unverified_numbers(summary_block["text"], all_tables),
        },
        "conclusion": {
            **conclusion_block,
            "warnings": [] if conclusion_block["edited"]
            else unverified_numbers(conclusion_block["text"], all_tables),
        },
        "sections": sections,
    }


def edited_blocks(report: dict[str, Any] | None) -> list[dict[str, str]]:
    """Блоки, которые человек правил: перед пересборкой о них спрашивают."""
    if not report:
        return []
    blocks = []
    if (report.get("summary") or {}).get("edited"):
        blocks.append({"id": "summary", "title": "Ключевые выводы"})
    for section in report.get("sections", []):
        if section.get("edited"):
            blocks.append({"id": section["id"], "title": section["title"]})
    if (report.get("conclusion") or {}).get("edited"):
        blocks.append({"id": "conclusion", "title": "Общий вывод"})
    return blocks
