"""История изменений конфигурации проекта для «Отменить» и «Вернуть» (P2, GAP-006).

Каждая запись проекта кладёт в историю состояние до неё, поэтому отмена —
это запись прежнего состояния новой ревизией: ревизии только растут, а
optimistic locking, проверка целостности и кэш отчётов работают как при
любой правке. История хранится в базе отдельно от документа проекта
(`project_history`): документ хэшируется в ключ кэша отчёта, и шаг отмены
стал бы частью конфигурации. Здесь — только вычисление шагов; читает и
пишет их `repository.metadata` в транзакции записи проекта.

Снимок хранит конфигурацию целиком, а описание структуры (`inspection`) —
только если шаг её менял. Отмена идёт строго с конца, поэтому к моменту
отмены шага текущее описание совпадает с тем, что было сразу после него, и
неизменённое описание брать неоткуда, кроме текущего проекта.
"""

from __future__ import annotations

from typing import Any

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
    "reports": "книги отчёта",
    "active_report_id": "выбранная книга",
    "formulas": "формулы",
    "codeframes": "кодификаторы открытых ответов",
    "analysis_cards": "карточки анализа",
    "analysis_models": "модели анализа",
    "table_reports": "таблицы",
    "label_overrides": "подписи из анкеты",
    "wave_view": "выбранная волна",
    "inspection": "описание переменных",
}
_IGNORED = {"revision", "updated_at"}


def trimmed(stacks: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {"undo": stacks["undo"][-MAX_STEPS:], "redo": stacks["redo"][-MAX_STEPS:]}


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


def summary(stacks: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "undo": len(stacks["undo"]),
        "redo": len(stacks["redo"]),
        "undo_sections": stacks["undo"][-1]["sections"] if stacks["undo"] else [],
        "redo_sections": stacks["redo"][-1]["sections"] if stacks["redo"] else [],
    }
