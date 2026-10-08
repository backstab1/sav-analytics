from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ..core.configuration_integrity import (
    ensure_not_referenced,
)
from ..core.report_books import inactive_books
from ..core.report_settings import (
    REPORT_SETTING_KEYS,
)
from .store import (
    ProjectNotFoundError,
    ProjectStore,
)


class ReportSetup(ProjectStore):
    """Свойства книги: баннеры, настройки отчёта, рассчитанные веса,
    фильтры и базы вопросов.
    """

    def create_banner(self, project_id: UUID, definition: dict) -> dict:
        project = self.get(project_id)
        banner_id = str(uuid4())
        project["configuration"]["banners"].append(
            {"id": banner_id, **self._banner_fields(definition)}
        )
        project["configuration"]["report_banner_id"] = banner_id
        self._apply_legacy_banner_report_settings(project, definition)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def update_banner(self, project_id: UUID, banner_id: UUID, definition: dict) -> dict:
        project = self.get(project_id)
        banners = project["configuration"]["banners"]
        try:
            index = next(
                index for index, item in enumerate(banners) if item["id"] == str(banner_id)
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(str(banner_id)) from exc
        banners[index] = {"id": str(banner_id), **self._banner_fields(definition)}
        if project["configuration"].get("report_banner_id") == str(banner_id):
            self._apply_legacy_banner_report_settings(project, definition)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def delete_banner(self, project_id: UUID, banner_id: UUID) -> dict:
        project = self.get(project_id)
        banners = project["configuration"]["banners"]
        filtered = [item for item in banners if item["id"] != str(banner_id)]
        if len(filtered) == len(banners):
            raise ProjectNotFoundError(str(banner_id))
        project["configuration"]["banners"] = filtered
        if project["configuration"].get("report_banner_id") == str(banner_id):
            project["configuration"]["report_banner_id"] = (
                filtered[-1]["id"] if filtered else None
            )
        # Таблица и неактивная книга с удалённым баннером остаются, как
        # отчёт: без разреза, только с колонкой Total.
        for book in inactive_books(project["configuration"]):
            if book.get("banner_id") == str(banner_id):
                book["banner_id"] = None
        for report in project["configuration"].get("table_reports", []):
            if report.get("banner_id") == str(banner_id):
                report["banner_id"] = None
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def banner(self, project_id: UUID, banner_id: UUID) -> tuple[dict, dict]:
        project = self.get(project_id)
        try:
            banner = next(
                item
                for item in project["configuration"]["banners"]
                if item["id"] == str(banner_id)
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(str(banner_id)) from exc
        return project, banner

    def assign_report_banner(
        self, project_id: UUID, banner_id: UUID | None
    ) -> dict:
        project = self.get(project_id)
        identifier = str(banner_id) if banner_id else None
        if identifier and not any(
            item["id"] == identifier
            for item in project["configuration"]["banners"]
        ):
            raise ProjectNotFoundError(identifier)
        project["configuration"]["report_banner_id"] = identifier
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def apply_autoreport_setup(
        self, project_id: UUID, banner_codes: list[str], weight: str | None
    ) -> tuple[dict, str | None]:
        """План автоотчёта в проекте — одной ревизией (решение 034).

        Разрез становится баннером «Автоотчёт» (свой, помеченный: повторная
        сборка обновляет его, а не плодит копии) и баннером отчёта; вес —
        весом отчёта. Это обычные настройки: их видно и можно поправить в
        «Отчётах». Возвращает проект и id баннера (None без разреза).
        """
        project = self.get(project_id)
        configuration = project["configuration"]
        banners = configuration["banners"]
        existing = next((item for item in banners if item.get("autoreport")), None)
        banner_id = None
        if banner_codes:
            blocks = [
                {"label": None, "sources": [{"kind": "question", "ref": code}]}
                for code in banner_codes
            ]
            if existing is None:
                existing = {"id": str(uuid4()), "name": "Автоотчёт", "autoreport": True}
                banners.append(existing)
            existing["blocks"] = blocks
            banner_id = existing["id"]
        configuration["report_banner_id"] = banner_id
        settings = dict(configuration["report_settings"])
        settings["weight_variable"] = weight
        if weight:
            settings["calculated_weight_id"] = None
        configuration["report_settings"] = settings
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project, coalesce="autoreport")
        return project, banner_id

    def update_report_settings(self, project_id: UUID, settings: dict) -> dict:
        project = self.get(project_id)
        project["configuration"]["report_settings"] = settings
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def create_calculated_weight(self, project_id: UUID, definition: dict) -> dict:
        project = self.get(project_id)
        project["configuration"]["calculated_weights"].append(
            {"id": str(uuid4()), **definition}
        )
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def update_calculated_weight(
        self, project_id: UUID, weight_id: UUID, definition: dict
    ) -> dict:
        project = self.get(project_id)
        weights = project["configuration"]["calculated_weights"]
        try:
            index = next(
                index for index, item in enumerate(weights) if item["id"] == str(weight_id)
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(str(weight_id)) from exc
        weights[index] = {"id": str(weight_id), **definition}
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def delete_calculated_weight(self, project_id: UUID, weight_id: UUID) -> dict:
        project = self.get(project_id)
        identifier = str(weight_id)
        weights = project["configuration"]["calculated_weights"]
        filtered = [item for item in weights if item["id"] != identifier]
        if len(filtered) == len(weights):
            raise ProjectNotFoundError(identifier)
        ensure_not_referenced(
            project["configuration"],
            "calculated_weight",
            identifier,
            "Рассчитанный вес",
        )
        project["configuration"]["calculated_weights"] = filtered
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def calculated_weight(self, project_id: UUID, weight_id: UUID) -> tuple[dict, dict]:
        project = self.get(project_id)
        try:
            weight = next(
                item
                for item in project["configuration"]["calculated_weights"]
                if item["id"] == str(weight_id)
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(str(weight_id)) from exc
        return project, weight

    def create_filter(self, project_id: UUID, definition: dict) -> dict:
        project = self.get(project_id)
        project["configuration"]["filters"].append({"id": str(uuid4()), **definition})
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def update_filter(self, project_id: UUID, filter_id: UUID, definition: dict) -> dict:
        project = self.get(project_id)
        filters = project["configuration"]["filters"]
        try:
            index = next(
                index for index, item in enumerate(filters) if item["id"] == str(filter_id)
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(str(filter_id)) from exc
        filters[index] = {"id": str(filter_id), **definition}
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def delete_filter(self, project_id: UUID, filter_id: UUID) -> dict:
        project = self.get(project_id)
        identifier = str(filter_id)
        filters = project["configuration"]["filters"]
        filtered = [item for item in filters if item["id"] != identifier]
        if len(filtered) == len(filters):
            raise ProjectNotFoundError(identifier)
        ensure_not_referenced(
            project["configuration"], "filter", identifier, "Фильтр"
        )
        project["configuration"]["filters"] = filtered
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def filter(self, project_id: UUID, filter_id: UUID) -> tuple[dict, dict]:
        project = self.get(project_id)
        try:
            definition = next(
                item
                for item in project["configuration"]["filters"]
                if item["id"] == str(filter_id)
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(str(filter_id)) from exc
        return project, definition

    def assign_question_base(
        self, project_id: UUID, code: str, filter_id: UUID | None
    ) -> dict:
        project = self.get(project_id)
        try:
            question = next(
                item for item in project["configuration"]["questions"] if item["code"] == code
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(code) from exc
        identifier = str(filter_id) if filter_id else None
        if identifier and not any(
            item["id"] == identifier for item in project["configuration"]["filters"]
        ):
            raise ProjectNotFoundError(identifier)
        question["base_filter_id"] = identifier
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def assign_report_filter(
        self, project_id: UUID, filter_id: UUID | None
    ) -> dict:
        project = self.get(project_id)
        identifier = str(filter_id) if filter_id else None
        if identifier and not any(
            item["id"] == identifier for item in project["configuration"]["filters"]
        ):
            raise ProjectNotFoundError(identifier)
        project["configuration"]["report_filter_id"] = identifier
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    @staticmethod
    def _banner_fields(definition: dict) -> dict:
        """Оставить от присланного баннера только то, что баннером и является.

        `BannerDefinition` наследует поля настроек отчёта, чтобы принимать запросы
        старых клиентов. Переносятся они в `report_settings`, а в баннер попадать не
        должны: иначе у одного значения снова окажется два места хранения.
        """
        return {
            key: value
            for key, value in definition.items()
            if key not in REPORT_SETTING_KEYS
        }

    @staticmethod
    def _apply_legacy_banner_report_settings(project: dict, definition: dict) -> None:
        updates = {
            key: definition[key]
            for key in REPORT_SETTING_KEYS
            if key in definition
        }
        if updates:
            project["configuration"]["report_settings"].update(updates)
