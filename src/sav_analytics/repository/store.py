from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from .. import project_history
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
from ..db import get_engine, sqlite_url
from ..project_models import CONFIGURATION_SCHEMA_VERSION, validate_stored_project
from .metadata import ProjectMetadata, RevisionMismatchError

STRUCTURE_VERSION = 6

# Внутри класса имя `list` занято методом библиотеки проектов.
HistoryStacks = dict[str, list[dict[str, Any]]]


ANALYSIS_QUESTION_TYPES = {"single_choice", "scale", "numeric"}


class ProjectNotFoundError(LookupError):
    pass


class InvalidUploadError(ValueError):
    pass


def default_database_url(root: Path) -> str:
    """База по умолчанию — файл SQLite рядом с каталогом проектов."""
    return sqlite_url(root.parent / "sav-analytics.db")


class ProjectStore:
    """Хранение проекта: документ и история — в базе, файлы — на томе под
    `root/<id>/`. Чтение, миграция схемы, запись ревизией и пересборка
    структуры. Остальные части `ProjectRepository` работают через эти методы.
    """

    def __init__(
        self,
        root: Path,
        max_upload_bytes: int,
        database_url: str | None = None,
        *,
        migrate: bool = True,
    ) -> None:
        self.root = root
        self.max_upload_bytes = max_upload_bytes
        self._drafts: dict[str, dict] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_url = database_url or default_database_url(root)
        self.engine = get_engine(self.database_url, migrate=migrate)
        self.metadata = ProjectMetadata(self.engine)

    def project_dir(self, project_id: UUID | str) -> Path:
        """Каталог файлов проекта на защищённом томе."""
        return self.root / str(project_id)

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
        return self.metadata.summaries()

    def get(self, project_id: UUID) -> dict:
        if str(project_id) in self._drafts:
            return copy.deepcopy(self._drafts[str(project_id)])
        project = self.metadata.load(str(project_id))
        if project is None:
            raise ProjectNotFoundError(str(project_id))
        stored_schema = int(project.get("configuration", {}).get("schema_version", 1))
        original = (
            copy.deepcopy(project) if stored_schema < CONFIGURATION_SCHEMA_VERSION else None
        )
        self._ensure_configuration(project)
        if original is not None:
            project = self._migrate_configuration(project_id, project, original, stored_schema)
        if project["configuration"].get("structure_version", 0) < STRUCTURE_VERSION:
            project = self._refresh_structure_data(project_id, project, history="reset")
        validate_stored_project(project)
        return project

    def stored_document(self, project_id: UUID | str) -> dict:
        """Документ как он лежит в базе — без миграции и пересборки."""
        project = self.metadata.load(str(project_id))
        if project is None:
            raise ProjectNotFoundError(str(project_id))
        return project

    def overwrite_stored_document(self, project_id: UUID | str, document: dict) -> None:
        """Записать документ мимо ревизии и истории (мигратор, тесты старых схем)."""
        self.metadata.overwrite(str(project_id), document)

    def history_stacks(self, project_id: UUID | str) -> HistoryStacks:
        return self.metadata.history(str(project_id))

    def _migrate_configuration(
        self, project_id: UUID, project: dict, original: dict, stored_schema: int
    ) -> dict:
        """Перевести проект на текущую схему и записать результат.

        `_ensure_configuration` уже собрал `report_settings` — из нового поля или,
        для схемы 1, с активного баннера. Здесь остаётся убрать прежние копии,
        чтобы у настройки было одно место, перевести колонки таблиц в блоки
        (схема 3) и зафиксировать версию.

        Перед первой перезаписью в `project_backups` кладётся копия исходного
        документа: если приложение придётся откатить на версию, которая новую
        схему не читает, восстанавливать будет откуда.
        """
        self.metadata.keep_backup(str(project_id), stored_schema, original)
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
        conflict = ConfigurationConflictError(
            "Проект уже изменён в другой вкладке или запросе. "
            "Обновите проект и повторите действие."
        )
        with self.metadata.writing(str(project_id)) as write:
            current = write.current()
            if current is None:
                raise ProjectNotFoundError(str(project_id))
            current_revision = int(current.get("configuration", {}).get("revision", 1))
            expected_revision = current_expected_revision()
            if expected_revision is None:
                expected_revision = int(project["configuration"].get("revision", 1))
            if expected_revision != current_revision:
                raise conflict
            project["configuration"]["revision"] = current_revision + 1
            project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
            validate_stored_project(project)
            try:
                write.replace(project, current_revision)
            except RevisionMismatchError as exc:
                raise conflict from exc
            write.update_history(
                lambda stacks: _next_history(stacks, current, project, history, coalesce)
            )


def _next_history(
    stacks: dict[str, list[dict[str, Any]]],
    before: dict,
    after: dict,
    history: str,
    coalesce: str | None,
) -> dict[str, list[dict[str, Any]]] | None:
    """Стеки отмены после записи; None — история начинается заново."""
    if history == "reset":
        return None
    step = project_history.snapshot(before, after)
    if history == "record":
        if step is None:
            return stacks
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
    return project_history.trimmed(stacks)


def _column_blocks(cols: list[dict], nested: bool) -> list[dict]:
    """Колонки таблицы схемы 2 — блоками схемы 3, с тем же разрезом.

    Флаг вложенности делил список на пары по порядку, непарный хвост шёл
    отдельным блоком; без флага каждая переменная — свой блок.
    """
    size = 2 if nested else 1
    return [{"sources": cols[index : index + size]} for index in range(0, len(cols), size)]
