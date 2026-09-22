"""История изменений конфигурации проекта для «Отменить» и «Вернуть» (P2, GAP-006).

Каждая запись проекта кладёт в историю состояние до неё, поэтому отмена —
это запись прежнего состояния новой ревизией: ревизии только растут, а
optimistic locking, проверка целостности и кэш отчётов работают как при
любой правке. История хранится рядом с проектом в `history.json`, а не в
`project.json`: тот хэшируется в ключ кэша отчёта, и шаг отмены стал бы
частью конфигурации.

Снимок хранит конфигурацию целиком, а описание структуры (`inspection`) —
только если шаг её менял. Отмена идёт строго с конца, поэтому к моменту
отмены шага текущее описание совпадает с тем, что было сразу после него, и
неизменённое описание брать неоткуда, кроме текущего проекта.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

HISTORY_FILE = "history.json"
MAX_STEPS = 20

SECTION_LABELS = {
    "questions": "структура вопросов",
    "recodings": "перекодировки",
    "banners": "баннеры",
    "filters": "фильтры и базы",
    "calculated_weights": "рассчитанные веса",
    "report_settings": "настройки отчёта",
    "report_banner_id": "баннер отчёта",
    "report_filter_id": "общий фильтр",
    "formulas": "формулы",
    "codeframes": "кодификаторы открытых ответов",
    "analysis_cards": "карточки анализа",
    "inspection": "описание переменных",
}
_IGNORED = {"revision", "updated_at"}


def load(project_dir: Path) -> dict[str, list[dict[str, Any]]]:
    path = project_dir / HISTORY_FILE
    if not path.is_file():
        return {"undo": [], "redo": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Испорченная история не мешает работать с проектом — она просто пуста.
        return {"undo": [], "redo": []}
    return {"undo": list(data.get("undo", [])), "redo": list(data.get("redo", []))}


def save(project_dir: Path, stacks: dict[str, list[dict[str, Any]]]) -> None:
    path = project_dir / HISTORY_FILE
    temporary = project_dir / f".{HISTORY_FILE}.tmp"
    temporary.write_text(
        json.dumps(
            {"undo": stacks["undo"][-MAX_STEPS:], "redo": stacks["redo"][-MAX_STEPS:]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def reset(project_dir: Path) -> None:
    (project_dir / HISTORY_FILE).unlink(missing_ok=True)


def snapshot(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    """Шаг истории: состояние `before` и названия того, что изменилось к `after`.

    None — если по существу ничего не изменилось: такой шаг отменять нечего.
    """
    sections = changed_sections(before, after)
    if not sections:
        return None
    inspection_changed = before.get("inspection") != after.get("inspection")
    return {
        "configuration": before["configuration"],
        "inspection": before.get("inspection") if inspection_changed else None,
        "sections": sections,
    }


def changed_sections(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    old = before.get("configuration") or {}
    new = after.get("configuration") or {}
    labels: list[str] = []
    for key in sorted(set(old) | set(new)):
        if key in _IGNORED or old.get(key) == new.get(key):
            continue
        label = SECTION_LABELS.get(key, "прочие настройки")
        if label not in labels:
            labels.append(label)
    if before.get("inspection") != after.get("inspection"):
        labels.append(SECTION_LABELS["inspection"])
    return labels


def restored(current: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    """Проект с конфигурацией шага; ревизия текущая — запись её поднимет."""
    configuration = {
        **step["configuration"],
        "revision": current["configuration"]["revision"],
    }
    project = {**current, "configuration": configuration}
    if step.get("inspection") is not None:
        project["inspection"] = step["inspection"]
    return project


def summary(project_dir: Path) -> dict[str, Any]:
    stacks = load(project_dir)
    return {
        "undo": len(stacks["undo"]),
        "redo": len(stacks["redo"]),
        "undo_sections": stacks["undo"][-1]["sections"] if stacks["undo"] else [],
        "redo_sections": stacks["redo"][-1]["sections"] if stacks["redo"] else [],
    }
