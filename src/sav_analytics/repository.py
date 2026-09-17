from __future__ import annotations

import copy
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
from .core.configuration_integrity import ConfigurationIntegrityError, ensure_not_referenced
from .core.formulas import (
    FormulaError,
    formula_question,
    formula_statistics,
    formula_variable,
    formula_variables,
    validate_formula,
)
from .core.not_applicable import NotApplicableConfirmationRequired, assess_not_applicable
from .core.report_settings import (
    DEFAULT_REPORT_SETTINGS,
    REPORT_SETTING_KEYS,
    resolved_report_settings,
)
from .core.review import CONFIRMED_RECOGNITIONS
from .core.sav_reader import SavReadError, inspect_sav, spss_missing_mask
from .core.tabular_import import TabularImportError, convert_to_sav, is_tabular
from .project_models import CONFIGURATION_SCHEMA_VERSION, validate_stored_project

STRUCTURE_VERSION = 6


ANALYSIS_QUESTION_TYPES = {"single_choice", "scale", "numeric"}

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
        tabular = is_tabular(original_filename)
        if not original_filename.lower().endswith(".sav") and not tabular:
            raise InvalidUploadError("Допускаются файлы SAV, CSV и TSV.")
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
        question = self._find_question(project, code)
        self._apply_question_changes(project_id, project, question, changes)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def update_questions(
        self,
        project_id: UUID,
        codes: list[str],
        changes: dict,
        *,
        confirm_recognition: bool = False,
    ) -> dict:
        """Одна правка для многих вопросов: одной ревизией и всё или ничего.

        Каждый вопрос проходит ту же проверку, что при правке по одному. Ошибка
        в любом отменяет всю правку: проект читается заново на каждый запрос,
        и запись происходит только после проверки всех вопросов.
        """
        project = self.get(project_id)
        identifier = changes.get("base_filter_id")
        if identifier and not any(
            item["id"] == identifier for item in project["configuration"]["filters"]
        ):
            raise ProjectNotFoundError(identifier)
        for code in dict.fromkeys(codes):
            question = self._find_question(project, code)
            try:
                self._apply_question_changes(
                    project_id,
                    project,
                    question,
                    dict(changes),
                    confirm_recognition=confirm_recognition,
                )
            except InvalidUploadError as exc:
                raise InvalidUploadError(f"Вопрос {code}: {exc}") from exc
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def copy_question_settings(
        self, project_id: UUID, source_code: str, codes: list[str], fields: list[str]
    ) -> dict:
        """Настройки одного вопроса — на выбранные того же типа, одной ревизией.

        Каждый вопрос проходит обычную проверку правки: NPS примерится к его
        шкале, NET-группы — к его типу. Ошибка в любом отменяет всё.
        """
        project = self.get(project_id)
        source = self._find_question(project, source_code)
        defaults = {
            "output_metrics": [],
            "nets": [],
            "special_values": [],
            "special_metric": "none",
            "base_filter_id": None,
        }
        for code in dict.fromkeys(codes):
            if code == source_code:
                continue
            target = self._find_question(project, code)
            if target["question_type"] != source["question_type"]:
                raise InvalidUploadError(
                    f"Вопрос {code} другого типа, чем {source_code}: настройки не переносятся."
                )
            changes = {
                field: copy.deepcopy(source.get(field, defaults[field])) for field in fields
            }
            if changes.get("nets") and source["question_type"] == "multiple_choice_dichotomy":
                # NET multiple собран из вариантов своего вопроса — у другого их нет.
                raise InvalidUploadError(
                    "NET-группы multiple-response состоят из его вариантов и не переносятся."
                )
            try:
                self._apply_question_changes(
                    project_id, project, target, changes, confirm_recognition=False
                )
            except InvalidUploadError as exc:
                raise InvalidUploadError(f"Вопрос {code}: {exc}") from exc
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    @staticmethod
    def _find_question(project: dict, code: str) -> dict:
        try:
            return next(
                item for item in project["configuration"]["questions"] if item["code"] == code
            )
        except StopIteration as exc:
            raise ProjectNotFoundError(code) from exc

    def _apply_question_changes(
        self,
        project_id: UUID,
        project: dict,
        question: dict,
        changes: dict,
        *,
        confirm_recognition: bool = True,
    ) -> None:
        """Проверить и применить правку вопроса к проекту в памяти, без записи.

        Сохранение карточки вопроса подтверждает распознавание; массовое
        включение и исключение — нет, для этого есть отдельное действие.
        """
        questions = project["configuration"]["questions"]
        code = question["code"]
        confirm_substantive = bool(changes.pop("confirm_substantive", False))
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
        if final_role == "weight":
            # «Объявить весом» — явное действие аналитика: оно и есть то
            # необходимое условие, которого не хватало, чтобы `ID` не проходил
            # весом молча. Пригодность распределения проверяется отдельно, при
            # выборе веса в настройках отчёта.
            if len(question["source_variables"]) != 1:
                raise InvalidUploadError("Весом можно объявить только одиночную переменную.")
            variable_name = question["source_variables"][0]
            variable = next(
                item
                for item in project["inspection"]["variables"]
                if item["name"] == variable_name
            )
            if variable["storage_type"] != "numeric":
                raise InvalidUploadError("Весом можно объявить только числовую переменную.")
            changes["included_in_report"] = False
        if (
            question.get("role") == "weight"
            and final_role != "weight"
            and (project["configuration"].get("report_settings") or {}).get("weight_variable")
            in question["source_variables"]
        ):
            # Снятие роли с выбранного веса оставило бы в настройках отчёта
            # переменную, которую сборка уже отвергнет: отказ пришёл бы позже
            # и в другом месте, чем действие, которое его вызвало.
            raise InvalidUploadError(
                "Эта переменная выбрана весом отчёта. Сначала смените вес в настройках отчёта."
            )
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
        if "nets" in changes:
            changes["nets"] = _validated_nets(question, final_type, changes["nets"])
        if "output_metrics" in changes:
            changes["output_metrics"] = _validated_output(final_type, changes["output_metrics"])
        if changes.get("not_applicable_values"):
            self._require_not_applicable_confirmation(
                project_id,
                project,
                [(question, changes["not_applicable_values"])],
                confirmed=confirm_substantive,
            )
        # Сохранение вопроса и есть проверка: пользователь открыл карточку,
        # увидел предупреждения распознавания — эвристический тип или состав
        # автоматически собранной группы — и подтвердил настройки.
        # Подтверждать нечего, если предупреждений нет: тогда распознавание
        # не трогаем, как и метаданные SPSS.
        if confirm_recognition and (
            question.get("recognition", "auto") not in CONFIRMED_RECOGNITIONS
            and question.get("warnings")
        ):
            question["recognition"] = "manual"
        question.update(changes)

    def mark_not_applicable(
        self, project_id: UUID, marks: list[dict], *, confirm_substantive: bool = False
    ) -> dict:
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
        self._require_not_applicable_confirmation(
            project_id,
            project,
            [(questions[mark["code"]], mark["values"]) for mark in marks if mark["values"]],
            confirmed=confirm_substantive,
        )
        for mark in marks:
            questions[mark["code"]]["not_applicable_values"] = list(mark["values"])
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def assess_not_applicable(self, project_id: UUID, code: str, values: list) -> dict:
        project, question = self.question(project_id, code)
        [assessment] = assess_not_applicable(
            self.source_path(project_id), project, [(question, list(values))]
        )
        return assessment.to_dict()

    def _require_not_applicable_confirmation(
        self,
        project_id: UUID,
        project: dict,
        marks: list[tuple[dict, list]],
        *,
        confirmed: bool,
    ) -> None:
        """Отказать, если пометка задевает содержательный код без подтверждения.

        Подписанная категория и частый код по данным неотличимы от заглушки,
        а ошибка стоит дорого: ответ молча исчезает из распределения и базы.
        Поэтому решение остаётся за аналитиком, но принимается явно.
        """
        if confirmed or not marks:
            return
        pending = [
            item
            for item in assess_not_applicable(self.source_path(project_id), project, marks)
            if item.requires_confirmation
        ]
        if pending:
            raise NotApplicableConfirmationRequired(pending)

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

    def add_analysis_card(self, project_id: UUID, a: dict, b: dict) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        for source in (a, b):
            if source["kind"] == "question":
                question = next(
                    (item for item in configuration["questions"] if item["code"] == source["ref"]),
                    None,
                )
                if question is None:
                    raise ProjectNotFoundError(source["ref"])
                if question["question_type"] not in ANALYSIS_QUESTION_TYPES or len(
                    question.get("source_variables") or []
                ) != 1:
                    raise InvalidUploadError(
                        f"{question['code']}: связь считается для одиночного выбора, "
                        "шкалы и числового вопроса."
                    )
            elif not any(item["id"] == source["ref"] for item in configuration["recodings"]):
                raise ProjectNotFoundError(source["ref"])
        configuration.setdefault("analysis_cards", []).append(
            {"id": str(uuid4()), "a": dict(a), "b": dict(b)}
        )
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def delete_analysis_card(self, project_id: UUID, card_id: UUID) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        cards = configuration.get("analysis_cards", [])
        kept = [item for item in cards if item["id"] != str(card_id)]
        if len(kept) == len(cards):
            raise ProjectNotFoundError(str(card_id))
        configuration["analysis_cards"] = kept
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def create_formula(self, project_id: UUID, definition: dict) -> dict:
        """Формула становится производной переменной и числовым вопросом."""
        project = self.get(project_id)
        record = {"id": str(uuid4()), **definition}
        counts = self._formula_counts(project_id, project, record)
        project["configuration"].setdefault("formulas", []).append(record)
        project["inspection"]["variables"].append(formula_variable(record, counts))
        project["configuration"]["questions"].append(formula_question(record, counts))
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def update_formula(self, project_id: UUID, formula_id: UUID, definition: dict) -> dict:
        project = self.get(project_id)
        identifier = str(formula_id)
        formulas = project["configuration"].get("formulas", [])
        index = next(
            (index for index, item in enumerate(formulas) if item["id"] == identifier), None
        )
        if index is None:
            raise ProjectNotFoundError(identifier)
        if definition["name"] != formulas[index]["name"]:
            # Имя — код вопроса, на него ссылаются баннеры и фильтры.
            raise InvalidUploadError("Имя формулы после создания не меняется.")
        record = {"id": identifier, **definition}
        counts = self._formula_counts(project_id, project, record)
        formulas[index] = record
        for item in project["inspection"]["variables"]:
            if item.get("formula_id") == identifier:
                item.update(formula_variable(record, counts))
        for question in project["configuration"]["questions"]:
            if question.get("formula_id") == identifier:
                question.update(
                    label=record["label"],
                    valid_count=counts["valid_count"],
                    missing_count=counts["missing_count"],
                )
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def delete_formula(self, project_id: UUID, formula_id: UUID) -> dict:
        project = self.get(project_id)
        identifier = str(formula_id)
        configuration = project["configuration"]
        formula = next(
            (item for item in configuration.get("formulas", []) if item["id"] == identifier),
            None,
        )
        if formula is None:
            raise ProjectNotFoundError(identifier)
        name = formula["name"]
        ensure_not_referenced(configuration, "question", name, "Формула")
        dependants = [
            item["name"]
            for item in configuration.get("formulas", [])
            if item["id"] != identifier and name in formula_variables(item["expression"])
        ]
        if dependants:
            raise ConfigurationIntegrityError(
                f"На формулу ссылаются другие формулы: {', '.join(dependants)}. "
                "Сначала измените их."
            )
        if any(item.get("source_variable") == name for item in configuration["recodings"]):
            raise ConfigurationIntegrityError(
                "Формула используется в перекодировке. Сначала удалите перекодировку."
            )
        if (configuration.get("report_settings") or {}).get("weight_variable") == name:
            raise ConfigurationIntegrityError(
                "Формула выбрана весом отчёта. Сначала смените вес в настройках отчёта."
            )
        configuration["formulas"] = [
            item for item in configuration["formulas"] if item["id"] != identifier
        ]
        project["inspection"]["variables"] = [
            item for item in project["inspection"]["variables"]
            if item.get("formula_id") != identifier
        ]
        configuration["questions"] = [
            item for item in configuration["questions"] if item.get("formula_id") != identifier
        ]
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def _formula_counts(self, project_id: UUID, project: dict, record: dict) -> dict[str, int]:
        try:
            validate_formula(record, project, formula_id=record["id"])
            return formula_statistics(self.source_path(project_id), record, project)
        except FormulaError as exc:
            raise InvalidUploadError(str(exc)) from exc

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
            item for item in project["inspection"]["variables"] if item.get("formula_id")
        )
        merged.extend(item for item in previous if item.get("formula_id"))
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


NET_QUESTION_TYPES = frozenset({"single_choice", "scale", "matrix", "multiple_choice_dichotomy"})


def _validated_nets(question: dict, question_type: str, nets: list[dict]) -> list[dict]:
    """Проверить NET-группы вопроса до сохранения.

    NET объединяет ответы одного вопроса. Для multiple-response ответы — это
    его варианты, поэтому чужая переменная в группе означает ошибку, а не
    расширение: такой NET посчитал бы долю по другому вопросу.
    """
    if not nets:
        return []
    if question_type not in NET_QUESTION_TYPES:
        raise InvalidUploadError(
            "NET-группы задаются для одиночного выбора, шкалы, матрицы и multiple-response."
        )
    labels = [item["label"].strip() for item in nets]
    if not all(labels):
        raise InvalidUploadError("У каждой NET-группы должно быть название.")
    if len({label.casefold() for label in labels}) != len(labels):
        raise InvalidUploadError("Названия NET-групп не должны повторяться.")
    if question_type == "multiple_choice_dichotomy":
        own = set(question["source_variables"])
        if any(str(value) not in own for item in nets for value in item["values"]):
            raise InvalidUploadError(
                "В NET-группу multiple-response входят только варианты этого вопроса."
            )
    return [
        {"label": label, "values": list(dict.fromkeys(item["values"]))}
        for label, item in zip(labels, nets, strict=True)
    ]


def _validated_output(question_type: str, metrics: list[str]) -> list[str]:
    """Свой набор вывода вопроса: только показатели его типа, в порядке отчёта.

    Порядок строк задаёт канонический список, а не порядок отметок, — как у
    набора отчёта: одинаковый выбор даёт одинаковую книгу и ключ кэша.
    """
    if not metrics:
        return []
    from .core.report_settings import NUMERIC_METRICS, SCALE_METRICS

    allowed = {
        "scale": SCALE_METRICS,
        "matrix": SCALE_METRICS,
        "numeric": NUMERIC_METRICS,
    }.get(question_type)
    if allowed is None:
        raise InvalidUploadError(
            "Свой набор вывода задаётся шкале, матрице и числовому вопросу."
        )
    if any(metric not in allowed for metric in metrics):
        raise InvalidUploadError("В наборе вывода есть показатель другого типа вопроса.")
    return [metric for metric in allowed if metric in set(metrics)]

