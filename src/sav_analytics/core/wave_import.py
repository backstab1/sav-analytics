"""Новая волна: разбор расхождений структуры до замены исходного файла (PQ.8).

Замена данных не должна ломать настроенный проект молча. Перед ней считается
разбор: какие переменные появились, какие исчезли, у каких изменились подпись,
подписи значений или тип хранения. Исчезнувшая переменная, на которую опирается
конфигурация — вопрос отчёта, перекодировка, фильтр, баннер, формула или
кодификатор, — это уже не расхождение, а поломка: такая замена отклоняется,
пока связь не снята.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VariableChange:
    name: str
    label: str
    changes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "changes": self.changes}


@dataclass
class WaveDiff:
    added: list[VariableChange]
    removed: list[VariableChange]
    changed: list[VariableChange]
    blocking: list[str]
    rows_before: int
    rows_after: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": [item.to_dict() for item in self.added],
            "removed": [item.to_dict() for item in self.removed],
            "changed": [item.to_dict() for item in self.changed],
            "blocking": self.blocking,
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "can_replace": not self.blocking,
        }


def compare_structures(project: dict[str, Any], inspection: dict[str, Any]) -> WaveDiff:
    """Сравнить структуру нового файла со структурой проекта."""
    before = {item["name"]: item for item in project["inspection"]["variables"]}
    after = {item["name"]: item for item in inspection["variables"]}
    derived = {
        name
        for name, item in before.items()
        if item.get("formula_id") or item.get("codeframe_id")
    }
    added = [
        VariableChange(name, item.get("label") or name, ["новая переменная"])
        for name, item in after.items()
        if name not in before
    ]
    removed = [
        VariableChange(name, item.get("label") or name, ["переменной больше нет"])
        for name, item in before.items()
        if name not in after and name not in derived
    ]
    changed = []
    for name, item in after.items():
        previous = before.get(name)
        if previous is None:
            continue
        notes = []
        if (previous.get("label") or "") != (item.get("label") or ""):
            notes.append(f"подпись: «{previous.get('label')}» → «{item.get('label')}»")
        if previous.get("storage_type") != item.get("storage_type"):
            notes.append(
                f"тип хранения: {previous.get('storage_type')} → {item.get('storage_type')}"
            )
        before_labels = {
            str(entry["value"]): entry["label"] for entry in previous.get("value_labels", [])
        }
        after_labels = {
            str(entry["value"]): entry["label"] for entry in item.get("value_labels", [])
        }
        if before_labels != after_labels:
            gone = sorted(set(before_labels) - set(after_labels))
            fresh = sorted(set(after_labels) - set(before_labels))
            renamed = [
                code
                for code in set(before_labels) & set(after_labels)
                if before_labels[code] != after_labels[code]
            ]
            parts = []
            if gone:
                parts.append(f"нет кодов {', '.join(gone)}")
            if fresh:
                parts.append(f"новые коды {', '.join(fresh)}")
            if renamed:
                parts.append(f"другие подписи у {', '.join(sorted(renamed))}")
            notes.append("подписи значений: " + "; ".join(parts))
        if notes:
            changed.append(VariableChange(name, item.get("label") or name, notes))
    return WaveDiff(
        added=sorted(added, key=lambda item: item.name),
        removed=sorted(removed, key=lambda item: item.name),
        changed=sorted(changed, key=lambda item: item.name),
        blocking=_blocking(project, set(after)),
        rows_before=int(project["inspection"].get("row_count", 0)),
        rows_after=int(inspection.get("row_count", 0)),
    )


def _blocking(project: dict[str, Any], available: set[str]) -> list[str]:
    """Связи конфигурации, которые новый файл разорвал бы."""
    configuration = project["configuration"]
    problems: list[str] = []
    for question in configuration["questions"]:
        if question.get("formula_id") or question.get("codeframe_id"):
            continue
        missing = [name for name in question.get("source_variables", []) if name not in available]
        if not missing:
            continue
        where = "в отчёте" if question.get("included_in_report") else "в структуре"
        problems.append(
            f"Вопрос {question['code']} {where} опирается на переменные, которых "
            f"в новом файле нет: {', '.join(missing)}."
        )
    for recoding in configuration.get("recodings", []):
        name = recoding.get("source_variable")
        if name and name not in available:
            problems.append(
                f"Перекодировка {recoding['code']} построена на переменной {name}, "
                "которой в новом файле нет."
            )
    for formula in configuration.get("formulas", []):
        from .formulas import formula_variables

        missing = [
            name
            for name in formula_variables(formula["expression"])
            if name not in available
            and not any(item["name"] == name for item in configuration.get("formulas", []))
        ]
        if missing:
            problems.append(
                f"Формула {formula['name']} опирается на переменные, которых в новом "
                f"файле нет: {', '.join(missing)}."
            )
    weight = (configuration.get("report_settings") or {}).get("weight_variable")
    if weight and weight not in available:
        problems.append(f"Вес отчёта {weight} в новом файле отсутствует.")
    return problems
