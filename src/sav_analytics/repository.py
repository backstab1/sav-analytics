from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from .core.sav_reader import SavReadError, inspect_sav

STRUCTURE_VERSION = 2


class ProjectNotFoundError(LookupError):
    pass


class InvalidUploadError(ValueError):
    pass


class ProjectRepository:
    def __init__(self, root: Path, max_upload_bytes: int) -> None:
        self.root = root
        self.max_upload_bytes = max_upload_bytes
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
                    "structure_version": STRUCTURE_VERSION,
                    "questions": inspection.to_dict()["questions"],
                    "recodings": [],
                    "banners": [],
                    "report_banner_id": None,
                    "filters": [],
                    "calculated_weights": [],
                    "report_filter_id": None,
                    "updated_at": created_at,
                },
            }
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
        self._ensure_configuration(project)
        if project["configuration"].get("structure_version", 0) < STRUCTURE_VERSION:
            project = self._refresh_structure_data(project_id, project)
        return project

    def update_question(self, project_id: UUID, code: str, changes: dict) -> dict:
        project = self.get(project_id)
        questions = project["configuration"]["questions"]
        try:
            question = next(item for item in questions if item["code"] == code)
        except StopIteration as exc:
            raise ProjectNotFoundError(code) from exc
        question.update(changes)
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
        recodings = project["configuration"]["recodings"]
        filtered = [item for item in recodings if item["id"] != str(recoding_id)]
        if len(filtered) == len(recodings):
            raise ProjectNotFoundError(str(recoding_id))
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
        merged = []
        editable_fields = {
            "label",
            "question_type",
            "role",
            "included_in_report",
            "special_values",
            "special_items",
            "base_filter_id",
        }
        for detected in refreshed["questions"]:
            configured = dict(detected)
            if detected["code"] in previous_by_code:
                old = previous_by_code[detected["code"]]
                configured.update({key: old[key] for key in editable_fields if key in old})
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
        project["configuration"]["banners"].append({"id": banner_id, **definition})
        project["configuration"]["report_banner_id"] = banner_id
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
        banners[index] = {"id": str(banner_id), **definition}
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
        if any(
            banner.get("calculated_weight_id") == identifier
            for banner in project["configuration"]["banners"]
        ):
            raise InvalidUploadError(
                "Рассчитанный вес используется в баннере и пока не может быть удалён."
            )
        weights = project["configuration"]["calculated_weights"]
        filtered = [item for item in weights if item["id"] != identifier]
        if len(filtered) == len(weights):
            raise ProjectNotFoundError(identifier)
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
        if any(
            question.get("base_filter_id") == identifier
            for question in project["configuration"]["questions"]
        ):
            raise InvalidUploadError(
                "Фильтр назначен как база вопроса и пока не может быть удалён."
            )
        if project["configuration"].get("report_filter_id") == identifier:
            raise InvalidUploadError(
                "Фильтр используется как общий фильтр отчёта и пока не может быть удалён."
            )
        filters = project["configuration"]["filters"]
        filtered = [item for item in filters if item["id"] != identifier]
        if len(filtered) == len(filters):
            raise ProjectNotFoundError(identifier)
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
            banner.setdefault(
                "compare_to_total",
                any(block.get("compare_to_total", False) for block in banner["blocks"]),
            )
            banner.setdefault(
                "compare_pairwise",
                any(block.get("compare_pairwise", False) for block in banner["blocks"]),
            )
        if "report_banner_id" not in project["configuration"]:
            project["configuration"]["report_banner_id"] = (
                banners[-1]["id"] if banners else None
            )
        project["configuration"].setdefault("filters", [])
        project["configuration"].setdefault("calculated_weights", [])
        project["configuration"].setdefault("report_filter_id", None)
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

    def _write_project(self, project_id: UUID, project: dict) -> None:
        project_dir = self.root / str(project_id)
        target = project_dir / "project.json"
        temporary = project_dir / ".project.json.tmp"
        temporary.write_text(
            json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, target)
