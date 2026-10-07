from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from ..core.formulas import formula_variable, read_project_frame
from ..core.questionnaire import value_key
from ..core.sav_reader import SavReadError
from ..core.tabular_import import TabularImportError, convert_to_sav, is_tabular
from ..core.waves import (
    WAVE_DIR,
    WAVE_MODES,
    WaveError,
    base_variables,
    convergence,
    propose_mapping,
    read_wave,
    stack_waves,
    unmatched_wave_variables,
    wave_values,
    wave_variable_name,
    wave_variable_of,
    wave_view,
)
from .store import InvalidUploadError, ProjectNotFoundError, ProjectStore


class WaveSources(ProjectStore):
    """Волны отдельными источниками (PQ.19): файл и сопоставление на волну,
    общий массив `source.sav` собирается из них."""

    def _wave_dir(self, project_id: UUID) -> Path:
        directory = self.root / str(project_id) / WAVE_DIR
        directory.mkdir(exist_ok=True)
        return directory

    def stage_wave(self, project_id: UUID, original_filename: str, source: BinaryIO) -> str:
        """Принять файл волны во временное место; возвращает id черновика."""
        self.get(project_id)
        tabular = is_tabular(original_filename)
        if not original_filename.lower().endswith(".sav") and not tabular:
            raise InvalidUploadError("Допускаются файлы SAV, CSV, TSV и XLSX.")
        staging_id = uuid4().hex
        directory = self._wave_dir(project_id)
        target = directory / f".staging-{staging_id}.sav"
        upload = (
            directory / f".staging-{staging_id}{Path(original_filename).suffix.lower()}"
            if tabular
            else target
        )
        size = 0
        try:
            with upload.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise InvalidUploadError("Размер файла превышает допустимый лимит.")
                    output.write(chunk)
            if size == 0:
                raise InvalidUploadError("Загружен пустой файл.")
            if tabular:
                try:
                    convert_to_sav(upload, target)
                finally:
                    upload.unlink(missing_ok=True)
            read_wave(target)
        except TabularImportError as exc:
            target.unlink(missing_ok=True)
            raise InvalidUploadError(str(exc)) from exc
        except (SavReadError, ValueError, OSError) as exc:
            target.unlink(missing_ok=True)
            if isinstance(exc, InvalidUploadError):
                raise
            raise InvalidUploadError("Файл волны не читается как SAV.") from exc
        (directory / f".staging-{staging_id}.name").write_text(
            Path(original_filename).name, encoding="utf-8"
        )
        return staging_id

    def _staged(self, project_id: UUID, staging_id: str) -> Path:
        if not staging_id.isalnum():
            raise ProjectNotFoundError(staging_id)
        path = self._wave_dir(project_id) / f".staging-{staging_id}.sav"
        if not path.is_file():
            raise ProjectNotFoundError(staging_id)
        return path

    def wave_preview(
        self,
        project_id: UUID,
        staging_id: str,
        mapping: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """Сопоставление и сходимость черновика волны.

        Без `mapping` — предложение по имени и подписи; с ним — проверка
        выбранного человеком.
        """
        project = self.get(project_id)
        wave = read_wave(self._staged(project_id, staging_id))
        rows = propose_mapping(project, wave)
        if mapping is not None:
            for row in rows:
                if row["target"] in mapping:
                    chosen = mapping[row["target"]]
                    if chosen != row["source"]:
                        row["source"] = chosen or None
                        row["how"] = "manual" if chosen else None
        unknown = [
            row["source"] for row in rows
            if row["source"] and row["source"] not in wave.frame.columns
        ]
        if unknown:
            raise InvalidUploadError(f"Переменной {unknown[0]} нет в файле волны.")
        base = {item["name"] for item in base_variables(project)}
        unmatched = unmatched_wave_variables(rows, wave)
        return {
            "staging_id": staging_id,
            "filename": (self._wave_dir(project_id) / f".staging-{staging_id}.name").read_text(
                encoding="utf-8"
            ),
            "mapping": rows,
            "wave_variables": [
                {"name": name, "label": wave.labels.get(name) or ""}
                for name in wave.frame.columns
            ],
            # Новые переменные волны: имя совпадает с переменной проекта —
            # значит, её сознательно не сопоставили, и добавить её нельзя.
            "unmatched": [
                {"name": name, "label": wave.labels.get(name) or "", "can_add": name not in base}
                for name in unmatched
            ],
            "convergence": convergence(project, wave, rows),
            "waves": project.get("waves") or [],
        }

    def add_wave(
        self,
        project_id: UUID,
        staging_id: str,
        label: str,
        mapping: dict[str, str | None],
        added: list[str],
    ) -> dict:
        """Добавить волну: файл, сопоставление, пересборка общего массива.

        Первая волна заводится из текущего исходника при добавлении второй.
        """
        preview = self.wave_preview(project_id, staging_id, mapping)
        if preview["convergence"]["blocking"]:
            raise InvalidUploadError(" ".join(preview["convergence"]["blocking"]))
        project = self.get(project_id)
        directory = self._wave_dir(project_id)
        project_dir = self.root / str(project_id)
        waves = list(project.get("waves") or [])
        meta = dict(project.get("waves_meta") or {})
        if not waves:
            # Волна, размеченная в данных, остаётся переменной волны: файл
            # добавит ей новое значение, а не заведёт вторую переменную.
            in_data = wave_variable_of(project)
            meta["in_data"] = bool(in_data)
        meta["variable"] = wave_variable_name(project)
        if not waves:
            first = {
                "id": uuid4().hex,
                "label": "Исходный файл" if meta["in_data"] else "Волна 1",
                "filename": project["original_filename"],
                "mapping": None,
                "added": [],
                "code": None if meta["in_data"] else 1.0,
            }
            shutil.copy2(project_dir / "source.sav", directory / f"{first['id']}.sav")
            waves.append(first)
        names = {item["name"] for item in base_variables(project)}
        allowed = {item["name"] for item in preview["unmatched"] if item["can_add"]}
        bad = [name for name in added if name not in allowed]
        if bad:
            raise InvalidUploadError(f"Переменную {bad[0]} нельзя добавить новой.")
        clash = [
            name for name in added
            if name in names or any(name in (wave.get("added") or []) for wave in waves)
        ]
        if clash:
            raise InvalidUploadError(f"Переменная {clash[0]} уже есть в проекте.")
        if any(wave["label"].casefold() == label.strip().casefold() for wave in waves):
            raise InvalidUploadError("Волна с таким названием уже есть.")
        # Код волны — следующий после всех известных значений переменной
        # волны: размеченных в данных и выданных файлам.
        known = [item["value"] for item in wave_values(project)] + [
            item.get("code") for item in waves
        ]
        numbers = []
        for value in known:
            try:
                numbers.append(float(value))
            except (TypeError, ValueError):
                continue
        wave = {
            "id": uuid4().hex,
            "label": label.strip(),
            "filename": preview["filename"],
            "mapping": {row["target"]: row["source"] for row in preview["mapping"]},
            "added": list(dict.fromkeys(added)),
            "code": float(int(max(numbers, default=0)) + 1),
        }
        self._staged(project_id, staging_id).replace(directory / f"{wave['id']}.sav")
        (directory / f".staging-{staging_id}.name").unlink(missing_ok=True)
        waves.append(wave)
        return self._restack(project_id, project, waves, meta)

    def set_wave_view(self, project_id: UUID, mode: str, value: Any = None) -> dict:
        """Выбрать волну для работы: одну, все вместе или сравнение.

        Хранится в конфигурации: книга Excel и ИИ отчёт собираются по той же
        волне, что на экране, а выбор откатывается «Отменить», как любая
        правка.
        """
        project = self.get(project_id)
        values = wave_values(project)
        if len(values) < 2:
            raise InvalidUploadError("В проекте одна волна — выбирать не из чего.")
        if mode not in WAVE_MODES:
            raise InvalidUploadError("Неизвестный режим волн.")
        view: dict[str, Any] = {"mode": mode}
        if mode == "wave":
            chosen = next(
                (item for item in values if value_key(item["value"]) == value_key(value)), None
            )
            if chosen is None:
                raise InvalidUploadError("Такой волны в проекте нет.")
            view["value"] = chosen["value"]
        project["configuration"]["wave_view"] = view
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def wave_overview(self, project_id: UUID) -> dict[str, Any]:
        """Волны для селектора: значение, подпись и число анкет в каждой."""
        project = self.get(project_id)
        variable = wave_variable_of(project)
        values = wave_values(project)
        counts: dict[str, int] = {}
        if variable and len(values) >= 2:
            frame = read_project_frame(self.source_path(project_id), project, [variable],
                                       all_waves=True)
            for item, count in frame[variable].value_counts(dropna=True).items():
                counts[value_key(item)] = int(count)
        return {
            "variable": variable,
            "view": wave_view(project),
            "values": [
                {**item, "count": counts.get(value_key(item["value"]), 0)} for item in values
            ],
            "total": sum(counts.values()),
            "waves": project.get("waves") or [],
        }

    def rename_wave(self, project_id: UUID, wave_id: str, label: str) -> dict:
        project = self.get(project_id)
        waves = [dict(item) for item in project.get("waves") or []]
        wave = next((item for item in waves if item["id"] == wave_id), None)
        if wave is None:
            raise ProjectNotFoundError(wave_id)
        if any(item["id"] != wave_id and item["label"].casefold() == label.strip().casefold()
               for item in waves):
            raise InvalidUploadError("Волна с таким названием уже есть.")
        wave["label"] = label.strip()
        return self._restack(project_id, project, waves, dict(project.get("waves_meta") or {}))

    def remove_wave(self, project_id: UUID, wave_id: str) -> dict:
        project = self.get(project_id)
        waves = [item for item in project.get("waves") or [] if item["id"] != wave_id]
        if len(waves) == len(project.get("waves") or []):
            raise ProjectNotFoundError(wave_id)
        if not waves:
            raise InvalidUploadError("Последнюю волну удалить нельзя.")
        if waves[0]["id"] != (project["waves"][0]["id"]):
            raise InvalidUploadError(
                "Первая волна задаёт структуру проекта: удалите сначала остальные."
            )
        result = self._restack(project_id, project, waves, dict(project.get("waves_meta") or {}))
        (self._wave_dir(project_id) / f"{wave_id}.sav").unlink(missing_ok=True)
        return result

    def _restack(
        self, project_id: UUID, project: dict, waves: list[dict], meta: dict
    ) -> dict:
        """Пересобрать общий массив, перечитать структуру и записать проект.

        История отмены начинается заново: прежние шаги относились к другим
        данным, как при замене исходника (PQ.8).
        """
        project_dir = self.root / str(project_id)
        target = project_dir / "source.sav"
        try:
            stack_waves(waves, project_dir, meta["variable"], target)
        except WaveError as exc:
            raise InvalidUploadError(str(exc)) from exc
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        project["source"] = {"size": target.stat().st_size, "sha256": digest}
        project["waves"] = waves
        project["waves_meta"] = meta
        project = self._merged_structure(project_id, project)
        for question in project["configuration"]["questions"]:
            if question["code"] == meta["variable"]:
                question["role"] = "wave"
                question["included_in_report"] = False
                question["recognition"] = "manual"
        for formula in project["configuration"].get("formulas", []):
            counts = self._formula_counts(project_id, project, formula)  # type: ignore[attr-defined]
            for variable in project["inspection"]["variables"]:
                if variable.get("formula_id") == formula["id"]:
                    variable.update(formula_variable(formula, counts))
            for question in project["configuration"]["questions"]:
                if question.get("formula_id") == formula["id"]:
                    question.update(
                        valid_count=counts["valid_count"], missing_count=counts["missing_count"]
                    )
        for codeframe in project["configuration"].get("codeframes", []):
            self._sync_codeframe(project_id, project, codeframe)  # type: ignore[attr-defined]
        self._write_project(project_id, project, history="reset")
        return project
