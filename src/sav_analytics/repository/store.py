from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import UUID

from .. import project_history
from ..atomic_file import read_text, replace_file
from ..configuration_revision import (
    ConfigurationConflictError,
    current_expected_revision,
)
from ..core.question_groups import carry_manual_groups
from ..core.questionnaire import apply_label_overrides
from ..core.report_books import ensure_books
from ..core.report_settings import (
    REPORT_SETTING_KEYS,
    resolved_report_settings,
)
from ..core.review import CONFIRMED_RECOGNITIONS
from ..core.sav_reader import inspect_sav
from ..project_models import CONFIGURATION_SCHEMA_VERSION, validate_stored_project

STRUCTURE_VERSION = 6


ANALYSIS_QUESTION_TYPES = {"single_choice", "scale", "numeric"}


class ProjectNotFoundError(LookupError):
    pass


class InvalidUploadError(ValueError):
    pass


class ProjectStore:
    """Хранение проекта на диске: чтение, миграция, запись ревизией, блокировки
    и пересборка структуры. Остальные части `ProjectRepository` работают
    через эти методы.
    """

    def __init__(self, root: Path, max_upload_bytes: int) -> None:
        self.root = root
        self.max_upload_bytes = max_upload_bytes
        self._project_locks: dict[str, Lock] = {}
        self._project_locks_guard = Lock()
        self._drafts: dict[str, dict] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def draft(self, project_id: UUID) -> Iterator[Callable[[], dict]]:
        """Черновик проекта: записи копятся в памяти, а не на диске.

        Внутри блока `get` отдаёт черновик, а `_write_project` кладёт в него
        результат без проверки ревизии и без истории. Так несколько правок
        проходят через обычные методы репозитория со всеми их проверками и
        потом пишутся одной ревизией — или не пишутся вовсе (проверка плана
        ассистента). Возвращает функцию, читающую текущий черновик.
        """
        identifier = str(project_id)
        self._drafts[identifier] = copy.deepcopy(self.get(project_id))
        try:
            yield lambda: copy.deepcopy(self._drafts[identifier])
        finally:
            self._drafts.pop(identifier, None)

    def list(self) -> list[dict]:
        projects = []
        for metadata_path in self.root.glob("*/project.json"):
            project = self._read(metadata_path)
            summary_keys = ("id", "name", "created_at", "original_filename")
            projects.append({key: project[key] for key in summary_keys})
        return sorted(projects, key=lambda item: item["created_at"], reverse=True)

    def get(self, project_id: UUID) -> dict:
        if str(project_id) in self._drafts:
            return copy.deepcopy(self._drafts[str(project_id)])
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
            project = self._refresh_structure_data(project_id, project, history="reset")
        validate_stored_project(project)
        return project

    def _migrate_configuration(
        self, project_id: UUID, project: dict, metadata_path: Path, stored_schema: int
    ) -> dict:
        """Перевести проект на текущую схему и записать результат.

        `_ensure_configuration` уже собрал `report_settings` — из нового поля или,
        для схемы 1, с активного баннера. Здесь остаётся убрать прежние копии,
        чтобы у настройки было одно место, перевести колонки таблиц в блоки
        (схема 3) и зафиксировать версию.

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
        if stored_schema < 3:
            for report in project["configuration"].get("table_reports", []):
                report["cols"] = _column_blocks(report.get("cols", []), report.pop("nested", False))
        # Схема 4: настроенная книга становится первой книгой списка.
        ensure_books(project["configuration"])
        project["configuration"]["schema_version"] = CONFIGURATION_SCHEMA_VERSION
        # Снимки прежней схемы отменой возвращать нельзя: история начинается заново.
        self._write_project(project_id, project, history="reset")
        return project

    @staticmethod
    def _find_question(project: dict, code: str) -> dict:
        try:
            return next(
                item for item in project["configuration"]["questions"] if item["code"] == code
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(code) from exc

    def refresh_structure(self, project_id: UUID) -> dict:
        project = self.get(project_id)
        return self._refresh_structure_data(project_id, project)

    def _refresh_structure_data(
        self, project_id: UUID, project: dict, *, history: str = "record"
    ) -> dict:
        """Перечитать структуру из SAV и записать проект."""
        project = self._merged_structure(project_id, project)
        self._write_project(project_id, project, history=history)
        return project

    def _merged_structure(self, project_id: UUID, project: dict) -> dict:
        """То же слияние без записи: чтобы за запрос проект писался один раз."""
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
            "ranking_encoding",
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
                # вопроса не изменился и не появилось новых предупреждений:
                # подтверждали именно их, остальное нужно проверять заново.
                if (
                    old.get("recognition") == "manual"
                    and detected.get("recognition") not in CONFIRMED_RECOGNITIONS
                    and old.get("source_variables") == detected.get("source_variables")
                    and set(detected.get("warnings") or []) <= set(old.get("warnings") or [])
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
        # Формулы не живут в SAV: перераспознавание их не находит, но терять
        # их нельзя — переносим производные переменные и вопросы как есть.
        refreshed["variables"].extend(
            item
            for item in project["inspection"]["variables"]
            if item.get("formula_id") or item.get("codeframe_id")
        )
        merged.extend(
            item for item in previous if item.get("formula_id") or item.get("codeframe_id")
        )
        # Ручные группы в SAV не записаны — ридер их не найдёт.
        merged = carry_manual_groups(
            previous, merged, {item["name"]: item for item in refreshed["variables"]}
        )
        project["inspection"] = refreshed
        project["configuration"]["questions"] = merged
        # Подписи из анкеты в SAV не записаны — накладываем их заново.
        apply_label_overrides(project)
        project["configuration"]["structure_version"] = STRUCTURE_VERSION
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        return project

    def original_path(self, project_id: UUID) -> Path | None:
        """Загруженный CSV или TSV, если проект создан из таблицы."""
        self.get(project_id)
        return next((self.root / str(project_id)).glob("original.*"), None)

    def source_path(self, project_id: UUID) -> Path:
        self.get(project_id)
        return self.root / str(project_id) / "source.sav"

    def report_cache_dir(self, project_id: UUID) -> Path:
        self.get(project_id)
        return self.root / str(project_id) / "reports"

    @staticmethod
    def _read(path: Path) -> dict:
        return json.loads(read_text(path))

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
        if project["configuration"].get("reports"):
            ensure_books(project["configuration"])
        for recoding in project["configuration"]["recodings"]:
            recoding.setdefault("mode", "ranges")

    def save_project(
        self, project_id: UUID, project: dict, *, coalesce: str | None = None
    ) -> None:
        """Записать проект, собранный вне обычных методов, — например из черновика.

        Та же запись, что у любой правки: проверка ревизии, история отмены,
        склейка серии по `coalesce`.
        """
        self._write_project(project_id, project, coalesce=coalesce)

    def _write_project(
        self,
        project_id: UUID,
        project: dict,
        *,
        history: str = "record",
        coalesce: str | None = None,
    ) -> None:
        """Записать проект, проверив ревизию, и вести историю отмены.

        `history`: `record` — обычная правка, прежнее состояние уходит в
        отмену, возврат очищается; `undo` и `redo` — шаг по истории;
        `reset` — история начинается заново (новые данные, миграция).

        `coalesce` склеивает серию однородных правок в один шаг: если
        последний шаг отмены записан с тем же ключом, новый не добавляется,
        и отмена возвращает состояние до начала серии. Так раскладка таблицы,
        которая сохраняется на каждый щелчок, не вытесняет из двадцати шагов
        истории всё остальное.
        """
        if str(project_id) in self._drafts:
            validate_stored_project(project)
            self._drafts[str(project_id)] = copy.deepcopy(project)
            return
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
            replace_file(temporary, target)
            self._record_history(project_dir, current, project, history, coalesce)

    @staticmethod
    def _record_history(
        project_dir: Path,
        before: dict,
        after: dict,
        history: str,
        coalesce: str | None = None,
    ) -> None:
        if history == "reset":
            project_history.reset(project_dir)
            return
        stacks = project_history.load(project_dir)
        step = project_history.snapshot(before, after)
        if history == "record":
            if step is None:
                return
            if coalesce and stacks["undo"] and stacks["undo"][-1].get("coalesce") == coalesce:
                # Серия продолжается: шаг до её начала уже лежит в отмене.
                stacks["redo"] = []
            else:
                if coalesce:
                    step["coalesce"] = coalesce
                stacks["undo"].append(step)
                stacks["redo"] = []
        else:
            back = "redo" if history == "undo" else "undo"
            if stacks[history]:
                stacks[history].pop()
            if step is not None:
                stacks[back].append(step)
        project_history.save(project_dir, stacks)

    def _project_lock(self, project_id: UUID) -> Lock:
        identifier = str(project_id)
        with self._project_locks_guard:
            return self._project_locks.setdefault(identifier, Lock())


def _column_blocks(cols: list[dict], nested: bool) -> list[dict]:
    """Колонки таблицы схемы 2 — блоками схемы 3, с тем же разрезом.

    Флаг вложенности делил список на пары по порядку, непарный хвост шёл
    отдельным блоком; без флага каждая переменная — свой блок.
    """
    size = 2 if nested else 1
    return [{"sources": cols[index : index + size]} for index in range(0, len(cols), size)]
