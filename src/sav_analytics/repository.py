from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import BinaryIO
from uuid import UUID, uuid4

import pandas as pd
import pyreadstat

from .configuration_revision import (
    ConfigurationConflictError,
    current_expected_revision,
)
from .core.configuration_integrity import ensure_not_referenced
from .core.report_settings import (
    DEFAULT_REPORT_SETTINGS,
    REPORT_SETTING_KEYS,
    resolved_report_settings,
)
from .core.sav_reader import SavReadError, inspect_sav, spss_missing_mask
from .project_models import CONFIGURATION_SCHEMA_VERSION, validate_stored_project

STRUCTURE_VERSION = 6


class ProjectNotFoundError(LookupError):
    pass


class InvalidUploadError(ValueError):
    pass


class ProjectRepository:
    def __init__(self, root: Path, max_upload_bytes: int) -> None:
        self.root = root
        self.max_upload_bytes = max_upload_bytes
        self._project_locks: dict[str, Lock] = {}
        self._project_locks_guard = Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, name: str, original_filename: str, source: BinaryIO) -> dict:
        if not original_filename.lower().endswith(".sav"):
            raise InvalidUploadError("Допускаются только файлы с расширением .sav.")
        project_id = uuid4()
        temporary = self.root / f".{project_id}.uploading"
        destination = self.root / str(project_id)
        temporary.mkdir()
        source_path = temporary / "source.sav"
        digest = hashlib.sha256()
        size = 0
        try:
            with source_path.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise InvalidUploadError("Размер SAV превышает допустимый лимит.")
                    digest.update(chunk)
                    output.write(chunk)

            if size == 0:
                raise InvalidUploadError("Загружен пустой файл.")
            inspection = inspect_sav(source_path)
            created_at = datetime.now(UTC).isoformat()
            project = {
                "id": str(project_id),
                "name": name.strip() or Path(original_filename).stem,
                "created_at": created_at,
                "original_filename": Path(original_filename).name,
                "source": {"size": size, "sha256": digest.hexdigest()},
                "inspection": inspection.to_dict(),
                "configuration": {
                    "schema_version": CONFIGURATION_SCHEMA_VERSION,
                    "structure_version": STRUCTURE_VERSION,
                    "revision": 1,
                    "questions": inspection.to_dict()["questions"],
                    "recodings": [],
                    "banners": [],
                    "report_banner_id": None,
                    "filters": [],
                    "calculated_weights": [],
                    "report_filter_id": None,
                    "report_settings": DEFAULT_REPORT_SETTINGS.copy(),
                    "updated_at": created_at,
                },
            }
            validate_stored_project(project)
            (temporary / "project.json").write_text(
                json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, destination)
            return project
        except (InvalidUploadError, SavReadError):
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def list(self) -> list[dict]:
        projects = []
        for metadata_path in self.root.glob("*/project.json"):
            project = self._read(metadata_path)
            summary_keys = ("id", "name", "created_at", "original_filename")
            projects.append({key: project[key] for key in summary_keys})
        return sorted(projects, key=lambda item: item["created_at"], reverse=True)

    def get(self, project_id: UUID) -> dict:
        metadata_path = self.root / str(project_id) / "project.json"
        if not metadata_path.is_file():
            raise ProjectNotFoundError(str(project_id))
        project = self._read(metadata_path)
        stored_schema = int(project.get("configuration", {}).get("schema_version", 1))
        self._ensure_configuration(project)
        if stored_schema < CONFIGURATION_SCHEMA_VERSION:
            project = self._migrate_configuration(
                project_id, project, metadata_path, stored_schema
            )
        if project["configuration"].get("structure_version", 0) < STRUCTURE_VERSION:
            project = self._refresh_structure_data(project_id, project)
        validate_stored_project(project)
        return project

    def _migrate_configuration(
        self, project_id: UUID, project: dict, metadata_path: Path, stored_schema: int
    ) -> dict:
        """Перевести проект на текущую схему и записать результат.

        `_ensure_configuration` уже собрал `report_settings` — из нового поля или,
        для схемы 1, с активного баннера. Здесь остаётся убрать прежние копии,
        чтобы у настройки было одно место, и зафиксировать версию.

        Перед первой перезаписью рядом кладётся копия исходного файла: если
        приложение придётся откатить на версию, которая новую схему не читает,
        восстанавливать будет откуда.
        """
        backup = metadata_path.with_suffix(f".v{stored_schema}.bak")
        if not backup.exists():
            shutil.copy2(metadata_path, backup)
        for banner in project["configuration"]["banners"]:
            for key in REPORT_SETTING_KEYS:
                banner.pop(key, None)
        project["configuration"]["schema_version"] = CONFIGURATION_SCHEMA_VERSION
        self._write_project(project_id, project)
        return project

    def update_question(self, project_id: UUID, code: str, changes: dict) -> dict:
        project = self.get(project_id)
        questions = project["configuration"]["questions"]
        try:
            question = next(item for item in questions if item["code"] == code)
        except StopIteration as exc:
            raise ProjectNotFoundError(code) from exc
        final_role = changes.get("role", question["role"])
        final_type = changes.get("question_type", question["question_type"])
        unsupported_types = {"multiple_choice_categorical", "ranking"}
        if changes.get("question_type") in unsupported_types:
            raise InvalidUploadError(
                "Этот тип вопроса пока не поддерживается в расчётах и отчёте."
            )
        if final_type in unsupported_types and changes.get("included_in_report") is True:
            raise InvalidUploadError(
                "Пока этот тип вопроса нельзя включить в отчёт."
            )
        if changes.get("not_applicable_values") and final_type == "multiple_choice_dichotomy":
            # У дихотомии выбор описывается counted_value, а не распределением
            # значений, поэтому пометка кода здесь ничего бы не изменила.
            raise InvalidUploadError(
                "Для multiple-response пропуск задаётся кодом выбранного ответа, "
                "а не пометкой «не применимо»."
            )
        if final_role == "wave":
            if final_type != "single_choice" or len(question["source_variables"]) != 1:
                raise InvalidUploadError("Переменная волны должна быть одиночным single choice.")
            if any(item["code"] != code and item.get("role") == "wave" for item in questions):
                raise InvalidUploadError("В проекте может быть только одна переменная волны.")
            changes["included_in_report"] = False
        special_metric = changes.get("special_metric", question.get("special_metric", "none"))
        if special_metric in {"nps", "csat"}:
            if final_type != "scale" or len(question["source_variables"]) != 1:
                raise InvalidUploadError("NPS и CSAT можно назначить только одиночной шкале.")
            variable_name = question["source_variables"][0]
            variable = next(
                item for item in project["inspection"]["variables"] if item["name"] == variable_name
            )
            labelled = [item["value"] for item in variable.get("value_labels", [])]
            frame, metadata = pyreadstat.read_sav(
                self.source_path(project_id),
                usecols=[variable_name],
                apply_value_formats=False,
                user_missing=True,
                dates_as_pandas_datetime=False,
            )
            observed_series = frame[variable_name]
            observed = observed_series.mask(
                spss_missing_mask(observed_series, variable_name, metadata)
            ).dropna().tolist()
            if labelled:
                labelled_series = pd.Series(labelled)
                labelled = labelled_series.mask(
                    spss_missing_mask(labelled_series, variable_name, metadata)
                ).dropna().tolist()
            try:
                values = {float(value) for value in [*labelled, *observed]}
            except (TypeError, ValueError) as exc:
                raise InvalidUploadError(
                    "Шкала NPS/CSAT должна содержать числовые значения."
                ) from exc
            expected = set(range(11)) if special_metric == "nps" else set(range(1, 6))
            label = "NPS" if special_metric == "nps" else "CSAT"
            if not values or not values <= expected:
                bounds = "0–10" if special_metric == "nps" else "1–5"
                raise InvalidUploadError(f"{label} можно назначить только шкале {bounds}.")
        # Сохранение вопроса и есть проверка: пользователь открыл карточку,
        # увидел состав автоматически собранной группы и подтвердил настройки.
        if question.get("recognition") == "auto_review":
            question["recognition"] = "manual"
        question.update(changes)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def mark_not_applicable(self, project_id: UUID, marks: list[dict]) -> dict:
        """Проставить коды «не применимо» сразу нескольким вопросам."""
        project = self.get(project_id)
        questions = {item["code"]: item for item in project["configuration"]["questions"]}
        for mark in marks:
            question = questions.get(mark["code"])
            if question is None:
                raise ProjectNotFoundError(mark["code"])
            if mark["values"] and question["question_type"] == "multiple_choice_dichotomy":
                raise InvalidUploadError(
                    "Для multiple-response пропуск задаётся кодом выбранного ответа, "
                    "а не пометкой «не применимо»."
                )
        for mark in marks:
            questions[mark["code"]]["not_applicable_values"] = list(mark["values"])
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def reorder_questions(self, project_id: UUID, codes: list[str]) -> dict:
        project = self.get(project_id)
        questions = project["configuration"]["questions"]
        current_codes = [item["code"] for item in questions]
        if len(codes) != len(set(codes)) or set(codes) != set(current_codes):
            raise InvalidUploadError("Новый порядок должен содержать все вопросы ровно один раз.")
        by_code = {item["code"]: item for item in questions}
        project["configuration"]["questions"] = [by_code[code] for code in codes]
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def question(self, project_id: UUID, code: str) -> tuple[dict, dict]:
        project = self.get(project_id)
        try:
            question = next(
                item for item in project["configuration"]["questions"] if item["code"] == code
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(code) from exc
        return project, question

    def create_recoding(self, project_id: UUID, definition: dict) -> dict:
        project = self.get(project_id)
        recodings = project["configuration"]["recodings"]
        self._ensure_unique_recode_code(recodings, definition["code"])
        recodings.append({"id": str(uuid4()), **definition})
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def update_recoding(self, project_id: UUID, recoding_id: UUID, definition: dict) -> dict:
        project = self.get(project_id)
        recodings = project["configuration"]["recodings"]
        try:
            index = next(
                index for index, item in enumerate(recodings) if item["id"] == str(recoding_id)
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(str(recoding_id)) from exc
        self._ensure_unique_recode_code(
            recodings, definition["code"], exclude_id=str(recoding_id)
        )
        recodings[index] = {"id": str(recoding_id), **definition}
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def delete_recoding(self, project_id: UUID, recoding_id: UUID) -> dict:
        project = self.get(project_id)
        identifier = str(recoding_id)
        recodings = project["configuration"]["recodings"]
        filtered = [item for item in recodings if item["id"] != identifier]
        if len(filtered) == len(recodings):
            raise ProjectNotFoundError(identifier)
        ensure_not_referenced(
            project["configuration"], "recoding", identifier, "Перекодировка"
        )
        project["configuration"]["recodings"] = filtered
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def recoding(self, project_id: UUID, recoding_id: UUID) -> tuple[dict, dict]:
        project = self.get(project_id)
        try:
            recoding = next(
                item
                for item in project["configuration"]["recodings"]
                if item["id"] == str(recoding_id)
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(str(recoding_id)) from exc
        return project, recoding

    def refresh_structure(self, project_id: UUID) -> dict:
        project = self.get(project_id)
        return self._refresh_structure_data(project_id, project)

    def _refresh_structure_data(self, project_id: UUID, project: dict) -> dict:
        source_path = self.root / str(project_id) / "source.sav"
        refreshed = inspect_sav(source_path).to_dict()
        previous = project["configuration"]["questions"]
        previous_by_code = {item["code"]: item for item in previous}
        inspected_by_code = {
            item["code"]: item for item in project["inspection"]["questions"]
        }
        merged = []
        editable_fields = {
            "label",
            "question_type",
            "role",
            "included_in_report",
            "special_values",
            "special_items",
            "special_metric",
            "not_applicable_values",
            "base_filter_id",
        }
        for detected in refreshed["questions"]:
            configured = dict(detected)
            if detected["code"] in previous_by_code:
                old = previous_by_code[detected["code"]]
                old_inspected = inspected_by_code.get(detected["code"], {})
                configured.update(
                    {
                        key: old[key]
                        for key in editable_fields
                        if key in old
                        and (key not in old_inspected or old[key] != old_inspected[key])
                    }
                )
                # Подтверждение проверки переживает перераспознавание, пока состав
                # группы не изменился: иначе проверять нужно заново.
                if (
                    old.get("recognition") == "manual"
                    and detected.get("recognition") == "auto_review"
                    and old.get("source_variables") == detected.get("source_variables")
                ):
                    configured["recognition"] = "manual"
            else:
                children = [
                    previous_by_code[name]
                    for name in detected["source_variables"]
                    if name in previous_by_code
                ]
                if children:
                    configured["included_in_report"] = all(
                        child["included_in_report"] for child in children
                    )
            merged.append(configured)
        project["inspection"] = refreshed
        project["configuration"]["questions"] = merged
        project["configuration"]["structure_version"] = STRUCTURE_VERSION
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

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

    def source_path(self, project_id: UUID) -> Path:
        self.get(project_id)
        return self.root / str(project_id) / "source.sav"

    def report_cache_dir(self, project_id: UUID) -> Path:
        self.get(project_id)
        return self.root / str(project_id) / "reports"

    @staticmethod
    def _read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _ensure_configuration(project: dict) -> None:
        if "configuration" not in project:
            project["configuration"] = {
                "questions": project["inspection"]["questions"],
                "recodings": [],
                "updated_at": project["created_at"],
            }
        project["configuration"].setdefault("recodings", [])
        project["configuration"].setdefault("banners", [])
        banners = project["configuration"]["banners"]
        for index, banner in enumerate(banners, start=1):
            if banner.get("name", "").strip().casefold() in {
                "основной",
                "основной баннер",
            }:
                banner["name"] = f"Баннер {index}"
        if "report_banner_id" not in project["configuration"]:
            project["configuration"]["report_banner_id"] = (
                banners[-1]["id"] if banners else None
            )
        if "report_settings" not in project["configuration"]:
            active_banner_id = project["configuration"].get("report_banner_id")
            active_banner = next(
                (
                    banner
                    for banner in banners
                    if banner.get("id") == active_banner_id
                ),
                banners[-1] if banners else None,
            )
            project["configuration"]["report_settings"] = resolved_report_settings(
                project["configuration"], active_banner
            )
        project["configuration"].setdefault("filters", [])
        project["configuration"].setdefault("calculated_weights", [])
        project["configuration"].setdefault("report_filter_id", None)
        project["configuration"].setdefault(
            "schema_version", CONFIGURATION_SCHEMA_VERSION
        )
        project["configuration"].setdefault("revision", 1)
        for recoding in project["configuration"]["recodings"]:
            recoding.setdefault("mode", "ranges")

    @staticmethod
    def _ensure_unique_recode_code(
        recodings: list[dict], code: str, exclude_id: str | None = None
    ) -> None:
        duplicate = any(
            item["code"].casefold() == code.casefold() and item["id"] != exclude_id
            for item in recodings
        )
        if duplicate:
            raise InvalidUploadError("Код перекодировки уже используется в этом проекте.")

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

    def _write_project(self, project_id: UUID, project: dict) -> None:
        project_dir = self.root / str(project_id)
        target = project_dir / "project.json"
        temporary = project_dir / ".project.json.tmp"
        with self._project_lock(project_id):
            current = self._read(target)
            current_revision = int(current.get("configuration", {}).get("revision", 1))
            expected_revision = current_expected_revision()
            if expected_revision is None:
                expected_revision = int(project["configuration"].get("revision", 1))
            if (
                expected_revision != current_revision
            ):
                raise ConfigurationConflictError(
                    "Проект уже изменён в другой вкладке или запросе. "
                    "Обновите проект и повторите действие."
                )
            project["configuration"]["revision"] = current_revision + 1
            project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
            validate_stored_project(project)
            temporary.write_text(
                json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, target)

    def _project_lock(self, project_id: UUID) -> Lock:
        identifier = str(project_id)
        with self._project_locks_guard:
            return self._project_locks.setdefault(identifier, Lock())
