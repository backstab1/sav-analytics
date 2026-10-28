from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from .. import project_history
from ..actor import current_actor_id
from ..core.configuration_integrity import (
    ConfigurationIntegrityError,
    validate_configuration_references,
)
from ..core.formulas import (
    formula_variable,
)
from ..core.questionnaire import apply_label_overrides, value_key
from ..core.report_books import ensure_books
from ..core.report_settings import (
    DEFAULT_REPORT_SETTINGS,
)
from ..core.sav_reader import inspect_sav
from ..core.survey_schema import (
    MAX_SCHEMA_BYTES,
    SCHEMA_EXTENSIONS,
    SCHEMA_FILE,
    SurveySchema,
    SurveySchemaError,
    completes,
    label_overrides,
    parse_schema,
)
from ..core.tabular_import import TabularImportError, convert_to_sav, enrich_sav, is_tabular
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

    def create(
        self,
        name: str,
        original_filename: str,
        source: BinaryIO,
        questionnaire: tuple[str, BinaryIO] | None = None,
    ) -> dict:
        """Создать проект из файла за один вызов: приём, разбор, запись."""
        staged = self.stage_upload(original_filename, source, questionnaire)
        return self.create_from_staged(name, staged)

    def stage_upload(
        self,
        original_filename: str,
        source: BinaryIO,
        questionnaire: tuple[str, BinaryIO] | None = None,
    ) -> dict:
        """Принять файл во временный каталог: размер и SHA-256 по ходу записи.

        Это единственная часть импорта внутри HTTP-запроса; разбор идёт
        отдельно (`create_from_staged`), в том числе фоновым заданием.
        Анкета JSON необязательна и проверяется сразу: непонятный файл
        лучше отклонить до очереди, чем после разбора массива.
        """
        schema_data = _read_schema_upload(questionnaire) if questionnaire else None
        tabular = is_tabular(original_filename)
        if not original_filename.lower().endswith(".sav") and not tabular:
            raise InvalidUploadError("Допускаются файлы SAV, CSV, TSV и XLSX.")
        project_id = uuid4()
        temporary = self.root / f".{project_id}.uploading"
        temporary.mkdir()
        # CSV хранится неизменным оригиналом, а расчёт идёт по SAV из него.
        upload_path = (
            temporary / f"original{Path(original_filename).suffix.lower()}"
            if tabular
            else temporary / "source.sav"
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
            if schema_data is not None:
                (temporary / SCHEMA_FILE).write_bytes(schema_data)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return {
            "schema_filename": Path(questionnaire[0]).name if questionnaire else None,
            "project_id": str(project_id),
            "original_filename": Path(original_filename).name,
            "upload": upload_path.name,
            "tabular": tabular,
            "size": size,
            "sha256": digest.hexdigest(),
        }

    def discard_staged(self, staged: dict) -> None:
        shutil.rmtree(self.root / f".{staged['project_id']}.uploading", ignore_errors=True)

    def create_from_staged(
        self,
        name: str,
        staged: dict,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict:
        """Разобрать принятый файл и записать проект; временный каталог уходит."""

        def step(index: int, stage: str) -> None:
            if progress is not None:
                progress(index, 4, stage)

        project_id = staged["project_id"]
        temporary = self.root / f".{project_id}.uploading"
        destination = self.root / project_id
        source_path = temporary / "source.sav"
        if not temporary.is_dir():
            raise InvalidUploadError("Загруженный файл не найден, загрузите его заново.")
        original_filename = staged["original_filename"]
        try:
            schema = _stored_schema(temporary)
            matched = 0
            if staged["tabular"]:
                step(0, "Переводим таблицу в SAV")
                try:
                    matched = convert_to_sav(temporary / staged["upload"], source_path, schema)
                except TabularImportError as exc:
                    raise InvalidUploadError(str(exc)) from exc
            elif schema is not None:
                step(0, "Дополняем SAV по анкете")
                matched = _enrich_source(temporary, schema)
            step(1, "Читаем метаданные")
            inspection = inspect_sav(source_path)
            step(2, "Распознаём структуру анкеты")
            created_at = datetime.now(UTC).isoformat()
            project = {
                "id": project_id,
                "name": name.strip() or Path(original_filename).stem,
                "created_at": created_at,
                "original_filename": original_filename,
                "source": {"size": staged["size"], "sha256": staged["sha256"]},
                **(
                    {
                        "survey_schema": {
                            "filename": staged.get("schema_filename") or SCHEMA_FILE,
                            "title": schema.title,
                            "matched": matched,
                        }
                    }
                    if schema is not None
                    else {}
                ),
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
            if schema is not None:
                completed = _apply_schema_labels(
                    project, schema, authoritative=staged["tabular"]
                )
                # Для таблицы важно, сколько столбцов описала анкета; для SAV —
                # сколько переменных она дополнила.
                project["survey_schema"]["completed"] = (
                    matched if staged["tabular"] else max(matched, completed)
                )
            ensure_books(project["configuration"])
            validate_stored_project(project)
            step(3, "Сохраняем проект")
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        # Файлы на месте раньше записи в базе: проект без файлов не появится
        # в библиотеке, а файлы без записи уберёт повторная попытка.
        try:
            self.metadata.insert(project, created_by=current_actor_id())
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return project

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
            # Анкета проекта подходит и новой волне: Excel волны получает те же
            # подписи, что получил первый.
            schema = _stored_schema(self.root / str(project_id))
            if tabular:
                try:
                    convert_to_sav(upload_path, source_path, schema)
                except TabularImportError as exc:
                    raise InvalidUploadError(str(exc)) from exc
            elif schema is not None:
                _enrich_source(staging, schema)
            inspection = inspect_sav(source_path).to_dict()
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return compare_structures(project, inspection), staging, digest.hexdigest(), size

    def apply_survey_schema(self, project_id: UUID, filename: str, data: bytes) -> dict:
        """Наложить анкету JSON на готовый проект, сохранив его настройки.

        Рабочий SAV собирается заново из загруженного файла: Excel или CSV —
        с подписями и кодами анкеты, SAV — с дополненными пробелами. Затем
        структура перечитывается тем же слиянием, что «Перераспознать
        структуру». Анкета остаётся у проекта, и новые волны получают её же.
        """
        if not filename.lower().endswith(SCHEMA_EXTENSIONS):
            raise InvalidUploadError("Анкета к массиву принимается в JSON.")
        try:
            schema = parse_schema(data)
        except SurveySchemaError as exc:
            raise InvalidUploadError(str(exc)) from exc
        project = self.get(project_id)
        project_dir = self.root / str(project_id)
        original = next(project_dir.glob("original.*"), None)
        staging = project_dir / f".schema-{uuid4()}"
        staging.mkdir()
        try:
            tabular = original is not None and is_tabular(original.name)
            # Расчёт всегда из того, что загрузил аналитик: повторная анкета
            # не накладывается на SAV, уже дополненный прежней.
            base = original if original is not None else project_dir / "source.sav"
            if tabular:
                try:
                    matched = convert_to_sav(base, staging / "source.sav", schema)
                except TabularImportError as exc:
                    raise InvalidUploadError(str(exc)) from exc
            else:
                shutil.copy2(base, staging / "original.sav")
                matched = _enrich_source_from(staging, schema)
            (staging / SCHEMA_FILE).write_bytes(data)
            diff = compare_structures(project, inspect_sav(staging / "source.sav").to_dict())
            if diff.blocking:
                raise InvalidUploadError(
                    "Анкета меняет структуру так, что рвутся связи проекта: "
                    + " ".join(diff.blocking)
                )
            if not tabular and not (staging / "original.sav").exists():
                # Анкете нечего дописать в SAV: исходник снова сам рабочий файл.
                for stale in project_dir.glob("original.*"):
                    stale.unlink()
            for item in staging.iterdir():
                os.replace(item, project_dir / item.name)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        project = self._merged_structure(project_id, project)
        completed = _apply_schema_labels(project, schema, authoritative=tabular)
        project["survey_schema"] = {
            "filename": Path(filename).name,
            "title": schema.title,
            "matched": matched,
            "completed": matched if tabular else max(matched, completed),
        }
        self._write_project(project_id, project)
        return project

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
            if (self.root / str(project_id) / SCHEMA_FILE).is_file():
                shutil.copy2(self.root / str(project_id) / SCHEMA_FILE, temporary / SCHEMA_FILE)
            # Результаты кодирования открытых ответов — файлы по ссылкам из
            # конфигурации: без них копия потеряла бы коды.
            for folder in ("coding", "waves"):
                # Кодирование открытых ответов и файлы волн — по ссылкам из
                # проекта: без них копия потеряла бы коды и не пересобрала волны.
                source_folder = self.root / str(project_id) / folder
                if source_folder.is_dir():
                    shutil.copytree(
                        source_folder, temporary / folder,
                        ignore=shutil.ignore_patterns(".staging-*"),
                    )
            created_at = datetime.now(UTC).isoformat()
            copied = json.loads(json.dumps(project))
            copied["id"] = str(copy_id)
            copied["name"] = f"Копия — {project['name']}"
            copied["created_at"] = created_at
            copied["configuration"]["revision"] = 1
            copied["configuration"]["updated_at"] = created_at
            validate_stored_project(copied)
            os.replace(temporary, self.root / str(copy_id))
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        try:
            self.metadata.insert(copied, created_by=current_actor_id())
        except Exception:
            shutil.rmtree(self.root / str(copy_id), ignore_errors=True)
            raise
        # История отмены у копии своя и пустая, но шаги исходного проекта
        # относились бы к его ревизиям.
        return copied

    def trash(self, project_id: UUID) -> None:
        """Убрать проект в корзину: он пропадает из библиотеки и не открывается.

        Файлы и собранные книги остаются на месте до окончательного удаления
        (`purge`), поэтому восстановление возвращает проект целиком — с теми
        же ревизиями и сборками.
        """
        self.get(project_id)
        if not self.metadata.set_trashed(str(project_id), datetime.now(UTC)):
            raise ProjectNotFoundError(str(project_id))

    def list_trash(self) -> list[dict]:
        return self.metadata.summaries(trashed=True)

    def restore(self, project_id: UUID) -> dict:
        if not self.metadata.set_trashed(str(project_id), None):
            raise ProjectNotFoundError(str(project_id))
        return self.get(project_id)

    def purge(self, project_id: UUID) -> None:
        """Окончательно удалить проект из корзины: запись, историю, файлы.

        Только из корзины: проект в библиотеке сначала туда попадает и
        какое-то время восстанавливается (`architecture.md` §6).
        """
        if self.metadata.load(str(project_id), trashed=True) is None:
            raise ProjectNotFoundError(str(project_id))
        from .. import report_versions
        from ..jobs.queue import JobQueue

        JobQueue(self.engine).delete_for_project(str(project_id))
        report_versions.delete_for_project(self.engine, str(project_id))
        self.metadata.delete(str(project_id))
        directory = self.project_dir(project_id)
        if directory.exists():
            # Каталог сначала переименовывается: если удаление прервётся,
            # остаток не примут за живой проект и повторная очистка его найдёт.
            doomed = self.root / f".{project_id}.purging"
            os.replace(directory, doomed)
            shutil.rmtree(doomed, ignore_errors=True)

    def purge_expired(self, retention_days: int) -> list[str]:
        """Удалить проекты, пролежавшие в корзине дольше срока хранения."""
        moment = datetime.now(UTC) - timedelta(days=retention_days)
        purged = []
        for identifier in self.metadata.trashed_before(moment):
            self.purge(UUID(identifier))
            purged.append(identifier)
        # Остатки прерванных удалений и загрузок, брошенных больше суток назад.
        for leftover in self.root.glob(".*.purging"):
            shutil.rmtree(leftover, ignore_errors=True)
        day_ago = (datetime.now(UTC) - timedelta(days=1)).timestamp()
        for leftover in self.root.glob(".*.uploading"):
            if leftover.stat().st_mtime < day_ago:
                shutil.rmtree(leftover, ignore_errors=True)
        return purged

    def history(self, project_id: UUID) -> dict:
        self.get(project_id)
        return project_history.summary(self.history_stacks(project_id))

    def undo(self, project_id: UUID) -> dict:
        """Отменить последний шаг: записать прежнюю конфигурацию новой ревизией."""
        return self._step_back(project_id, "undo")

    def redo(self, project_id: UUID) -> dict:
        return self._step_back(project_id, "redo")

    def _step_back(self, project_id: UUID, direction: str) -> dict:
        project = self.get(project_id)
        stacks = self.history_stacks(project_id)
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
            self.metadata.reset_history(str(project_id))
            raise InvalidUploadError(
                f"Шаг нельзя вернуть: {exc} История отмены очищена."
            ) from exc
        self._write_project(project_id, restored, history=direction)
        return self.get(project_id)


def _read_schema_upload(questionnaire: tuple[str, BinaryIO]) -> bytes:
    filename, stream = questionnaire
    if not filename.lower().endswith(SCHEMA_EXTENSIONS):
        raise InvalidUploadError("Анкета к массиву принимается в JSON.")
    data = stream.read(MAX_SCHEMA_BYTES + 1)
    try:
        parse_schema(data)
    except SurveySchemaError as exc:
        raise InvalidUploadError(str(exc)) from exc
    return data


def _stored_schema(directory: Path) -> SurveySchema | None:
    path = directory / SCHEMA_FILE
    if not path.is_file():
        return None
    try:
        return parse_schema(path.read_bytes())
    except SurveySchemaError as exc:
        raise InvalidUploadError(str(exc)) from exc


def _enrich_source_from(directory: Path, schema: SurveySchema) -> int:
    """`source.sav` из `original.sav` по анкете; без дополнений — копия.

    Оригинал остаётся, только если SAV переписан: иначе рабочий файл и есть
    загруженный, как у проекта без анкеты.
    """
    original = directory / "original.sav"
    try:
        enriched = enrich_sav(original, directory / "source.sav", schema)
    except TabularImportError as exc:
        raise InvalidUploadError(str(exc)) from exc
    if not enriched:
        os.replace(original, directory / "source.sav")
    return enriched


def _enrich_source(directory: Path, schema: SurveySchema) -> int:
    """Дополнить `source.sav` по анкете; загруженный SAV остаётся `original.sav`.

    Если анкете нечего добавить, файл не переписывается и оригинала нет —
    как у проекта без анкеты.
    """
    source = directory / "source.sav"
    original = directory / "original.sav"
    os.replace(source, original)
    try:
        enriched = enrich_sav(original, source, schema)
    except BaseException as exc:
        source.unlink(missing_ok=True)
        os.replace(original, source)
        if isinstance(exc, TabularImportError):
            raise InvalidUploadError(str(exc)) from exc
        raise
    if not enriched:
        os.replace(original, source)
    return enriched


def _apply_schema_labels(project: dict, schema: SurveySchema, *, authoritative: bool) -> int:
    """Полные тексты анкеты поверх подписей, которые SAV хранит сокращёнными.

    Они живут в `label_overrides`, как подписи из анкеты DOCX: так их не
    сотрёт перераспознавание структуры и не потеряет новая волна. Вопрос
    получает текст анкеты сразу: группа — текст вопроса, а не общее начало
    сокращённых подписей пунктов. Возвращает, сколько переменных получили
    подписи из анкеты.

    Правки аналитика сильнее анкеты: подпись, уже записанная в
    `label_overrides`, и подпись вопроса, изменённая в «Данных», остаются.
    """
    configuration = project["configuration"]
    existing = configuration.get("label_overrides") or {}
    overrides = label_overrides(
        project["inspection"]["variables"], schema, authoritative=authoritative
    )

    def full_label(question: dict) -> str | None:
        if question["source_variables"] == [question["code"]]:
            return (overrides.get(question["code"]) or {}).get("label")
        return schema.question_texts.get(question["code"])

    detected = {item["code"]: item["label"] for item in project["inspection"]["questions"]}
    for question in project["inspection"]["questions"]:
        full = full_label(question)
        if full and completes(question["label"], full, authoritative=authoritative):
            question["label"] = full
    for question in configuration["questions"]:
        full = full_label(question)
        edited = question["code"] in detected and question["label"] != detected[question["code"]]
        if full and not edited and completes(question["label"], full, authoritative=authoritative):
            question["label"] = full
    if overrides:
        merged = {name: dict(entry) for name, entry in overrides.items()}
        for name, entry in existing.items():
            target = merged.setdefault(name, {})
            if entry.get("label"):
                target["label"] = entry["label"]
            if entry.get("values"):
                kept = {value_key(item["value"]) for item in entry["values"]}
                target["values"] = [
                    *(item for item in target.get("values") or []
                      if value_key(item["value"]) not in kept),
                    *entry["values"],
                ]
        configuration["label_overrides"] = merged
        apply_label_overrides(project)
    return len(overrides)
