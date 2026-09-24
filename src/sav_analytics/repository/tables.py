"""Сохранённые таблицы раздела «Таблицы» (PQ.7 для экрана кросстаба)."""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .store import InvalidUploadError, ProjectNotFoundError, ProjectStore

_DEFAULT_NAME = re.compile(r"^Таблица (\d+)$")


class TableReports(ProjectStore):
    """Именованные раскладки кросстаба: у каждой свои строки, разрез, фильтр
    и вид. Книгу отчёта они не меняют — это рабочие таблицы экрана."""

    def create_table_report(self, project_id: UUID, name: str | None, layout: dict) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        self._check_table_layout(configuration, layout)
        reports = configuration.setdefault("table_reports", [])
        reports.append(
            {"id": str(uuid4()), "name": name or _next_default_name(reports), **layout}
        )
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def update_table_report(self, project_id: UUID, report_id: UUID, layout: dict) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        report = self._find_table_report(configuration, report_id)
        self._check_table_layout(configuration, layout)
        report.update(layout)
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        # Раскладка сохраняется на каждый щелчок: серия правок одной таблицы —
        # один шаг отмены.
        self._write_project(project_id, project, coalesce=f"table:{report['id']}")
        return project

    def rename_table_report(self, project_id: UUID, report_id: UUID, name: str) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        self._find_table_report(configuration, report_id)["name"] = name
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def copy_table_report(self, project_id: UUID, report_id: UUID) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        original = self._find_table_report(configuration, report_id)
        reports = configuration["table_reports"]
        duplicate = copy.deepcopy(original)
        duplicate["id"] = str(uuid4())
        duplicate["name"] = _copy_name(original["name"], reports)
        # Копия встаёт сразу за оригиналом, а не в конец списка.
        reports.insert(reports.index(original) + 1, duplicate)
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def delete_table_report(self, project_id: UUID, report_id: UUID) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        report = self._find_table_report(configuration, report_id)
        configuration["table_reports"].remove(report)
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    @staticmethod
    def _find_table_report(configuration: dict, report_id: UUID | str) -> dict:
        identifier = str(report_id)
        for report in configuration.get("table_reports", []):
            if report["id"] == identifier:
                return report
        raise ProjectNotFoundError(identifier)

    @staticmethod
    def _check_table_layout(configuration: dict, layout: dict) -> None:
        """Раскладка ссылается только на то, что есть в проекте.

        Проверка та же, что у целостности конфигурации, но с понятной причиной
        до записи: иначе ответом была бы общая «повреждённые ссылки».
        """
        questions = {item["code"] for item in configuration["questions"]}
        recodings = {item["id"] for item in configuration["recodings"]}
        missing = [code for code in layout["rows"] if code not in questions]
        missing += [code for code in layout["nets"] if code not in questions]
        missing += [
            source["ref"]
            for source in layout["cols"]
            if source["ref"] not in (questions if source["kind"] == "question" else recodings)
        ]
        if missing:
            raise InvalidUploadError(
                f"В проекте нет переменных таблицы: {', '.join(dict.fromkeys(missing))}."
            )
        banner_id = layout.get("banner_id")
        if banner_id and not any(item["id"] == banner_id for item in configuration["banners"]):
            raise InvalidUploadError("Баннер таблицы не найден.")
        filter_id = layout.get("filter_id")
        if filter_id and not any(item["id"] == filter_id for item in configuration["filters"]):
            raise InvalidUploadError("Фильтр таблицы не найден.")


def _next_default_name(reports: list[dict]) -> str:
    taken = {
        int(match.group(1))
        for report in reports
        if (match := _DEFAULT_NAME.match(report.get("name", "")))
    }
    number = 1
    while number in taken:
        number += 1
    return f"Таблица {number}"


def _copy_name(name: str, reports: list[dict]) -> str:
    names = {report.get("name") for report in reports}
    candidate = f"{name} (копия)"
    number = 2
    while candidate in names:
        candidate = f"{name} (копия {number})"
        number += 1
    return candidate[:120]
