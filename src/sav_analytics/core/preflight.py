"""Проверка конфигурации до запуска сборки отчёта.

До появления этого модуля единственным способом узнать о проблеме был запуск
фоновой задачи: аналитик ждал сборку и получал `ReportError` из её середины.
Часть проблем не сообщала о себе вообще — пустая база вопроса давала нули во
всех ячейках, а категория баннера с нулевой базой молча исчезала из Excel.

Разделение на два класса задано `requirements.md` §8: ошибки блокируют
генерацию, предупреждения — нет. Отсутствие подписи вопроса предупреждением
не считается: по общему правилу вместо неё выводится код переменной.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .filtering import evaluate_filter_frame
from .reporting.data import ReportData, prepare_report_data
from .reporting.models import ReportError

# `requirements.md` §6: при достижении 50 столбцов показывается неблокирующее
# предупреждение о ширине отчёта.
WIDE_BANNER_COLUMNS = 50


@dataclass(frozen=True, slots=True)
class PreflightFinding:
    code: str
    message: str
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "scope": self.scope}


@dataclass(frozen=True, slots=True)
class PreflightReport:
    errors: list[PreflightFinding]
    warnings: list[PreflightFinding]

    @property
    def can_prepare(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_prepare": self.can_prepare,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
        }


class PreflightBlockedError(ValueError):
    """Сборка запрошена при блокирующих ошибках конфигурации."""

    def __init__(self, report: PreflightReport) -> None:
        # Текст остаётся строкой: клиент показывает `detail` как есть, а полный
        # разбор берёт отдельным запросом к preflight.
        super().__init__(
            "Проверка конфигурации не пройдена. "
            + " ".join(item.message for item in report.errors)
        )
        self.report = report


def run_preflight(path: str | Path, project: dict[str, Any]) -> PreflightReport:
    errors: list[PreflightFinding] = []
    warnings: list[PreflightFinding] = []

    try:
        data = prepare_report_data(path, project)
    except ReportError as exc:
        # Структурная проблема: без готовых данных остальные проверки посчитать
        # не на чем, поэтому она возвращается одна и сразу.
        return PreflightReport(
            errors=[PreflightFinding("REPORT_NOT_BUILDABLE", str(exc))],
            warnings=warnings,
        )

    if not data.questions:
        errors.append(
            PreflightFinding(
                "NO_QUESTIONS_INCLUDED",
                "В отчёт не включён ни один вопрос. Отметьте вопросы на экране структуры.",
            )
        )

    errors.extend(_empty_question_bases(data, project))
    warnings.extend(_banner_warnings(data))
    return PreflightReport(errors=errors, warnings=warnings)


def _empty_question_bases(
    data: ReportData, project: dict[str, Any]
) -> list[PreflightFinding]:
    """Найти вопросы, у которых после общего фильтра не осталось ни одного респондента.

    Такой вопрос не ломает сборку: он выводит нули во всех ячейках, и отличить
    их от честного нуля по файлу нельзя.
    """
    findings: list[PreflightFinding] = []
    report_mask = data.columns[0]["mask"]
    cache: dict[str, bool] = {}
    for question in data.questions:
        filter_id = question.get("base_filter_id")
        if not filter_id:
            continue
        if filter_id not in cache:
            definition = data.filters.get(filter_id)
            if definition is None:
                # Целостность ссылок проверяется раньше; сюда можно попасть
                # только при гонке, и тогда это ошибка, а не пустая база.
                findings.append(
                    PreflightFinding(
                        "QUESTION_BASE_NOT_FOUND",
                        f"База вопроса {question['code']} не найдена.",
                        question["code"],
                    )
                )
                continue
            mask = evaluate_filter_frame(definition, project, data.frame) & report_mask
            cache[filter_id] = bool(mask.any())
        if not cache[filter_id]:
            name = data.filters[filter_id].get("name") or filter_id
            findings.append(
                PreflightFinding(
                    "EMPTY_QUESTION_BASE",
                    f"База «{name}» вопроса {question['code']} не оставляет "
                    "ни одного респондента.",
                    question["code"],
                )
            )
    return findings


def _banner_warnings(data: ReportData) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    minimum_base = int(data.statistical_settings["minimum_base"])

    for label in data.empty_columns:
        findings.append(
            PreflightFinding(
                "EMPTY_BANNER_CATEGORY",
                f"Категория баннера «{label}» пуста и в Excel не попадёт.",
                label,
            )
        )

    for column in data.columns[1:]:
        if 0 < column["base"] < minimum_base:
            findings.append(
                PreflightFinding(
                    "SMALL_COLUMN_BASE",
                    f"База колонки «{column['label']}» равна {column['base']} "
                    f"при пороге {minimum_base}: её результаты будут серыми.",
                    column["label"],
                )
            )

    if len(data.columns) >= WIDE_BANNER_COLUMNS:
        findings.append(
            PreflightFinding(
                "WIDE_BANNER",
                f"В отчёте {len(data.columns)} столбцов — с таким баннером книга "
                "становится неудобной для чтения.",
            )
        )
    return findings


__all__ = [
    "WIDE_BANNER_COLUMNS",
    "PreflightBlockedError",
    "PreflightFinding",
    "PreflightReport",
    "run_preflight",
]
