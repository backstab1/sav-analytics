from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from .. import project_history
from ..core.configuration_integrity import (
    ConfigurationIntegrityError,
    validate_configuration_references,
)
from ..core.formulas import (
    formula_variable,
)
from ..core.report_settings import (
    DEFAULT_REPORT_SETTINGS,
)
from ..core.sav_reader import SavReadError, inspect_sav
from ..core.tabular_import import TabularImportError, convert_to_sav, is_tabular
from ..core.wave_import import WaveDiff, compare_structures
from ..project_models import CONFIGURATION_SCHEMA_VERSION, validate_stored_project
from .store import (
    STRUCTURE_VERSION,
    InvalidUploadError,
    ProjectNotFoundError,
    ProjectStore,
)


class ProjectLifecycle(ProjectStore):
    """Проект целиком: создание из файла, новая волна, библиотека проектов
    и отмена шагов.
    """

    def create(self, name: str, original_filename: str, source: BinaryIO) -> dict:
        tabular = is_tabular(original_filename)
        if not original_filename.lower().endswith(".sav") and not tabular:
            raise InvalidUploadError("Допускаются файлы SAV, CSV, TSV и XLSX.")
        project_id = uuid4()
        temporary = self.root / f".{project_id}.uploading"
        destination = self.root / str(project_id)
        temporary.mkdir()
        source_path = temporary / "source.sav"
        # CSV хранится неизменным оригиналом, а расчёт идёт по SAV из него.
        upload_path = (
            temporary / f"original{Path(original_filename).suffix.lower()}"
            if tabular
            else source_path
        )
        digest = hashlib.sha256()
        size = 0
        try:
            with upload_path.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise InvalidUploadError("Размер SAV превышает допустимый лимит.")
                    digest.update(chunk)
                    output.write(chunk)

            if size == 0:
                raise InvalidUploadError("Загружен пустой файл.")
            if tabular:
                try:
                    convert_to_sav(upload_path, source_path)
                except TabularImportError as exc:
                    raise InvalidUploadError(str(exc)) from exc
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

    def inspect_new_wave(
        self, project_id: UUID, original_filename: str, source: BinaryIO
    ) -> tuple[WaveDiff, Path, str, int]:
        """Разобрать новый файл для проекта: расхождения структуры и сам файл.

        Файл остаётся во временной папке проекта: если аналитик подтвердит
        замену, он станет источником, если нет — будет удалён.
        """
        project = self.get(project_id)
        tabular = is_tabular(original_filename)
        if not original_filename.lower().endswith(".sav") and not tabular:
            raise InvalidUploadError("Допускаются файлы SAV, CSV, TSV и XLSX.")
        staging = self.root / str(project_id) / f".wave-{uuid4()}"
        staging.mkdir(parents=True)
        source_path = staging / "source.sav"
        suffix = Path(original_filename).suffix.lower()
        upload_path = staging / f"original{suffix}" if tabular else source_path
        digest = hashlib.sha256()
        size = 0
        try:
            with upload_path.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise InvalidUploadError("Размер файла превышает допустимый лимит.")
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise InvalidUploadError("Загружен пустой файл.")
            if tabular:
                try:
                    convert_to_sav(upload_path, source_path)
                except TabularImportError as exc:
                    raise InvalidUploadError(str(exc)) from exc
            inspection = inspect_sav(source_path).to_dict()
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return compare_structures(project, inspection), staging, digest.hexdigest(), size

    def replace_source(
        self, project_id: UUID, original_filename: str, source: BinaryIO
    ) -> tuple[dict, WaveDiff]:
        """Заменить исходный файл проекта новой волной, сохранив настройки.

        Настройки вопросов, перекодировки, фильтры, баннеры, формулы и
        кодификаторы остаются; структура перечитывается из нового файла тем же
        слиянием, что «Перераспознать структуру». Замена отклоняется, если
        новый файл разрывает связи конфигурации.
        """
        diff, staging, digest, size = self.inspect_new_wave(
            project_id, original_filename, source
        )
        project_dir = self.root / str(project_id)
        try:
            if diff.blocking:
                raise InvalidUploadError(
                    "Новый файл разрывает связи проекта: " + " ".join(diff.blocking)
                )
            project = self.get(project_id)
            for original in project_dir.glob("original.*"):
                original.unlink()
            for item in staging.iterdir():
                os.replace(item, project_dir / item.name)
            project["source"] = {"size": size, "sha256": digest}
            project["original_filename"] = Path(original_filename).name
            # Проект пишется один раз за запрос: при If-Match вторая запись
            # увидела бы уже другую ревизию и отказала конфликтом.
            project = self._merged_structure(project_id, project)
            for formula in project["configuration"].get("formulas", []):
                counts = self._formula_counts(project_id, project, formula)
                for variable in project["inspection"]["variables"]:
                    if variable.get("formula_id") == formula["id"]:
                        variable.update(formula_variable(formula, counts))
                for question in project["configuration"]["questions"]:
                    if question.get("formula_id") == formula["id"]:
                        question.update(
                            valid_count=counts["valid_count"],
                            missing_count=counts["missing_count"],
                        )
            for codeframe in project["configuration"].get("codeframes", []):
                self._sync_codeframe(project_id, project, codeframe)
            # Прежние шаги относятся к другим данным: отмена вернула бы
            # настройки, рассчитанные на исходник, которого больше нет.
            self._write_project(project_id, project, history="reset")
            return project, diff
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def rename(self, project_id: UUID, name: str) -> dict:
        project = self.get(project_id)
        cleaned = name.strip()
        if not cleaned:
            raise InvalidUploadError("Название проекта не может быть пустым.")
        project["name"] = cleaned
        self._write_project(project_id, project)
        return project

    def duplicate(self, project_id: UUID) -> dict:
        """Копия проекта: тот же SAV и те же настройки, но своя история.

        Собранные отчёты не копируются — они принадлежат ревизиям исходного
        проекта, а у копии ревизия начинается заново.
        """
        project = self.get(project_id)
        copy_id = uuid4()
        temporary = self.root / f".{copy_id}.copying"
        temporary.mkdir()
        try:
            shutil.copy2(
                self.root / str(project_id) / "source.sav", temporary / "source.sav"
            )
            for original in (self.root / str(project_id)).glob("original.*"):
                shutil.copy2(original, temporary / original.name)
            created_at = datetime.now(UTC).isoformat()
            copied = json.loads(json.dumps(project))
            copied["id"] = str(copy_id)
            copied["name"] = f"Копия — {project['name']}"
            copied["created_at"] = created_at
            copied["configuration"]["revision"] = 1
            copied["configuration"]["updated_at"] = created_at
            validate_stored_project(copied)
            (temporary / "project.json").write_text(
                json.dumps(copied, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.root / str(copy_id))
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return copied

    def trash(self, project_id: UUID) -> None:
        """Убрать проект в корзину: каталог целиком, вместе с отчётами.

        Безвозвратного удаления здесь нет — проект восстанавливается тем же
        каталогом, с теми же ревизиями и собранными книгами.
        """
        self.get(project_id)
        trash_root = self.root / ".trash"
        trash_root.mkdir(exist_ok=True)
        target = trash_root / str(project_id)
        with self._project_lock(project_id):
            try:
                os.replace(self.root / str(project_id), target)
            except OSError as exc:
                raise InvalidUploadError(
                    "Проект сейчас используется, например собирается отчёт. Повторите позже."
                ) from exc
            (target / "trashed.json").write_text(
                json.dumps({"trashed_at": datetime.now(UTC).isoformat()}), encoding="utf-8"
            )

    def list_trash(self) -> list[dict]:
        items = []
        for metadata_path in (self.root / ".trash").glob("*/project.json"):
            project = self._read(metadata_path)
            marker = metadata_path.parent / "trashed.json"
            trashed_at = (
                self._read(marker)["trashed_at"] if marker.is_file() else project["created_at"]
            )
            items.append(
                {
                    **{
                        key: project[key]
                        for key in ("id", "name", "created_at", "original_filename")
                    },
                    "trashed_at": trashed_at,
                }
            )
        return sorted(items, key=lambda item: item["trashed_at"], reverse=True)

    def restore(self, project_id: UUID) -> dict:
        source = self.root / ".trash" / str(project_id)
        if not (source / "project.json").is_file():
            raise ProjectNotFoundError(str(project_id))
        target = self.root / str(project_id)
        if target.exists():
            raise InvalidUploadError("Проект с тем же идентификатором уже есть в библиотеке.")
        with self._project_lock(project_id):
            try:
                os.replace(source, target)
            except OSError as exc:
                raise InvalidUploadError(
                    "Проект сейчас не удаётся восстановить. Повторите позже."
                ) from exc
            (target / "trashed.json").unlink(missing_ok=True)
        return self.get(project_id)

    def history(self, project_id: UUID) -> dict:
        self.get(project_id)
        return project_history.summary(self.root / str(project_id))

    def undo(self, project_id: UUID) -> dict:
        """Отменить последний шаг: записать прежнюю конфигурацию новой ревизией."""
        return self._step_back(project_id, "undo")

    def redo(self, project_id: UUID) -> dict:
        return self._step_back(project_id, "redo")

    def _step_back(self, project_id: UUID, direction: str) -> dict:
        project = self.get(project_id)
        stacks = project_history.load(self.root / str(project_id))
        if not stacks[direction]:
            raise InvalidUploadError(
                "Отменять нечего." if direction == "undo" else "Возвращать нечего."
            )
        restored = project_history.restored(project, stacks[direction][-1])
        # Прежнее состояние было целостным, но проверка стоит: история могла
        # пережить правку проекта в обход приложения.
        try:
            validate_configuration_references(restored["configuration"])
        except ConfigurationIntegrityError as exc:
            project_history.reset(self.root / str(project_id))
            raise InvalidUploadError(
                f"Шаг нельзя вернуть: {exc} История отмены очищена."
            ) from exc
        self._write_project(project_id, restored, history=direction)
        return self.get(project_id)
