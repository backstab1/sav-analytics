"""Что изменил план ассистента и как это вернуть, не трогая чужие правки.

Отмена проекта (решение 023) идёт строго с конца. План ассистента нужно уметь
откатить и тогда, когда после него человек успел поправить что-то другое.
Поэтому при применении запоминается разница по объектам: перекодировка,
фильтр, таблица, вопрос — каждый по своему ключу. Откат возвращает только
эти объекты и только если с тех пор их никто не менял; иначе называет
конфликт, а не затирает чужую работу.
"""

from __future__ import annotations

import copy
from typing import Any

from ..core.configuration_integrity import (
    ConfigurationIntegrityError,
    validate_configuration_references,
)
from ..project_history import SECTION_LABELS

_IGNORED = {"revision", "updated_at"}
_IDENTITY_KEYS = ("id", "code", "name")


def _identity(items: list[Any]) -> str | None:
    if not all(isinstance(item, dict) for item in items):
        return None
    for key in _IDENTITY_KEYS:
        if all(key in item for item in items):
            return key
    return None


def _section_diff(before: Any, after: Any) -> dict | None:
    if before == after:
        return None
    if isinstance(before, list) and isinstance(after, list):
        key = _identity([*before, *after])
        if key is not None:
            old = {item[key]: item for item in before}
            new = {item[key]: item for item in after}
            entries = [
                {"key": identity, "before": old.get(identity), "after": new.get(identity)}
                for identity in dict.fromkeys([*old, *new])
                if old.get(identity) != new.get(identity)
            ]
            return {"kind": "entities", "identity": key, "entries": entries}
    return {"kind": "value", "before": before, "after": after}


def _diff(before: dict, after: dict) -> dict:
    sections = {}
    for key in dict.fromkeys([*before, *after]):
        if key in _IGNORED:
            continue
        change = _section_diff(before.get(key), after.get(key))
        if change is not None:
            sections[key] = change
    return sections


def project_changes(before: dict, after: dict) -> dict:
    return {
        "configuration": _diff(before["configuration"], after["configuration"]),
        "inspection": _diff(before["inspection"], after["inspection"]),
    }


def changed_sections(changes: dict) -> list[str]:
    return [SECTION_LABELS.get(key, key) for key in changes["configuration"]]


class RevertConflict(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _entity_name(section: str, entity: dict | None, identity: Any) -> str:
    label = SECTION_LABELS.get(section, section)
    if entity:
        for key in ("code", "name", "label"):
            if entity.get(key):
                return f"{label}: {entity[key]}"
    return f"{label}: {identity}"


def _revert_part(part: dict, changes: dict, reasons: list[str]) -> None:
    for section, change in changes.items():
        if change["kind"] == "value":
            if part.get(section) != change["after"]:
                reasons.append(f"{SECTION_LABELS.get(section, section)} изменены после плана")
                continue
            if change["before"] is None:
                part.pop(section, None)
            else:
                part[section] = copy.deepcopy(change["before"])
            continue
        items = part.setdefault(section, [])
        key = change["identity"]
        for entry in change["entries"]:
            position = next(
                (index for index, item in enumerate(items) if item.get(key) == entry["key"]),
                None,
            )
            current = items[position] if position is not None else None
            if current != entry["after"]:
                name = _entity_name(section, entry["after"] or entry["before"], entry["key"])
                reasons.append(f"{name} — изменено после плана")
                continue
            if entry["before"] is None:
                if position is not None:
                    items.pop(position)
            elif position is None:
                items.append(copy.deepcopy(entry["before"]))
            else:
                items[position] = copy.deepcopy(entry["before"])


def reverted_project(project: dict, changes: dict) -> dict:
    """Проект без изменений плана. `RevertConflict`, если вернуть нельзя чисто."""
    result = copy.deepcopy(project)
    reasons: list[str] = []
    _revert_part(result["configuration"], changes["configuration"], reasons)
    _revert_part(result["inspection"], changes["inspection"], reasons)
    if not reasons:
        try:
            validate_configuration_references(result["configuration"])
        except ConfigurationIntegrityError as exc:
            reasons.append(f"На созданное планом уже ссылаются: {exc}")
    if reasons:
        raise RevertConflict(reasons)
    return result
