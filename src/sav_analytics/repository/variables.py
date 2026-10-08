from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pandas as pd

from ..core.configuration_integrity import (
    ConfigurationIntegrityError,
    ensure_not_referenced,
)
from ..core.formulas import (
    FormulaError,
    formula_question,
    formula_statistics,
    formula_variable,
    formula_variables,
    read_project_frame,
    validate_formula,
)
from ..core.open_text import (
    TONE_VALUES,
    TONES,
    CodeframeError,
    answered_mask,
    codeframe_columns,
    codeframe_text_variable,
    load_coding,
    normalize_answer,
    store_coding,
    theme_variable,
    tone_variable,
    validate_codeframe,
)
from ..core.report_books import book_settings
from .store import (
    InvalidUploadError,
    ProjectNotFoundError,
    ProjectStore,
)


class DerivedVariables(ProjectStore):
    """Производные переменные: перекодировки, кодификаторы открытых ответов
    и формулы.
    """

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

    def create_codeframe(self, project_id: UUID, question_code: str) -> dict:
        project = self.get(project_id)
        self._add_codeframe(project, question_code)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def create_codeframes(self, project_id: UUID, question_codes: list[str]) -> dict:
        """Кодификаторы для отмеченных открытых вопросов — одной ревизией.

        Вопрос, у которого кодификатор уже есть, пропускается.
        """
        project = self.get(project_id)
        framed = {item["question_code"] for item in project["configuration"].get("codeframes", [])}
        for code in dict.fromkeys(question_codes):
            if code not in framed:
                self._add_codeframe(project, code)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def _add_codeframe(self, project: dict, question_code: str) -> dict:
        configuration = project["configuration"]
        question = self._find_question(project, question_code)
        if question["question_type"] != "open_text" or len(question["source_variables"]) != 1:
            raise InvalidUploadError(f"{question_code}: кодируется только открытый вопрос.")
        codeframes = configuration.setdefault("codeframes", [])
        if any(item["question_code"] == question_code for item in codeframes):
            raise InvalidUploadError("У этого вопроса уже есть кодификатор.")
        taken = {item["name"].lower() for item in project["inspection"]["variables"]} | {
            item["code"].lower() for item in configuration["questions"]
        } | {item["code"].lower() for item in codeframes}
        base = re.sub(r"[^A-Za-z0-9_]", "_", question_code)
        if not re.match(r"^[A-Za-z]", base):
            base = f"T_{base}"
        code = f"{base}_C"
        index = 2
        while code.lower() in taken or any(
            name.lower().startswith(f"{code.lower()}_") for name in taken
        ):
            code = f"{base}_C{index}"
            index += 1
        codeframe = {
            "id": str(uuid4()),
            "question_code": question_code,
            "code": code,
            "label": f"Коды: {question['label']}",
            "themes": [],
            "next_number": 1,
            "instruction": "",
            "multi": True,
            "sentiment": True,
            "other_threshold": 0.01,
            "coding_ref": None,
        }
        codeframes.append(codeframe)
        return codeframe

    def update_codeframe(
        self,
        project_id: UUID,
        codeframe_id: UUID,
        label: str,
        themes: list[dict],
        settings: dict | None = None,
    ) -> dict:
        """Справочник кодов и настройки кодирования.

        Удалённый код снимается со всех ответов — и в кодах модели, и в
        словаре правок: иначе он вернулся бы с новой волной.
        """
        project = self.get(project_id)
        codeframe = self._find_codeframe(project, codeframe_id)
        rebuilt, next_number = _rebuilt_themes(codeframe, themes)
        candidate = {**codeframe, "label": label, "themes": rebuilt, "next_number": next_number}
        for key, value in (settings or {}).items():
            if value is not None:
                candidate[key] = value
        try:
            validate_codeframe(candidate)
        except CodeframeError as exc:
            raise InvalidUploadError(str(exc)) from exc
        removed = {theme["id"] for theme in codeframe["themes"]} - {
            theme["id"] for theme in rebuilt
        }
        if removed and codeframe.get("coding_ref"):
            coding = self.coding(project_id, codeframe)
            for entry in coding["answers"].values():
                entry["codes"] = [code for code in entry.get("codes", []) if code not in removed]
            for key, codes in coding["dictionary"].items():
                coding["dictionary"][key] = [code for code in codes if code not in removed]
            candidate["coding_ref"] = store_coding(self.root / str(project_id), coding)
        codeframe.update(candidate)
        self._sync_codeframe(project_id, project, codeframe)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def coding(self, project_id: UUID, codeframe: dict) -> dict:
        return load_coding(self.root / str(project_id), codeframe.get("coding_ref"))

    def set_answer_codes(
        self, project_id: UUID, codeframe_id: UUID, key: str, codes: list[str] | None
    ) -> dict:
        """Правка человека: коды ответа и запись в словарь «текст → коды».

        `codes = None` снимает правку — ответу возвращаются коды модели.
        """
        project = self.get(project_id)
        codeframe = self._find_codeframe(project, codeframe_id)
        known = {theme["id"] for theme in codeframe["themes"]}
        coding = self.coding(project_id, codeframe)
        key = normalize_answer(key)
        if codes is None:
            coding["dictionary"].pop(key, None)
            entry = coding["answers"].get(key)
            if entry and entry.get("source") == "manual":
                coding["answers"].pop(key)
        else:
            unknown = [code for code in codes if code not in known]
            if unknown:
                raise ProjectNotFoundError(unknown[0])
            if not codeframe.get("multi", True) and len(codes) > 1:
                raise InvalidUploadError("В этом кодификаторе у ответа один код.")
            codes = list(dict.fromkeys(codes))
            coding["dictionary"][key] = codes
            previous = coding["answers"].get(key) or {}
            coding["answers"][key] = {**previous, "codes": codes, "source": "manual", "low": False}
        codeframe["coding_ref"] = store_coding(self.root / str(project_id), coding)
        self._sync_codeframe(project_id, project, codeframe)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def set_answer_tone(
        self, project_id: UUID, codeframe_id: UUID, key: str, tone: str | None
    ) -> dict:
        """Правка тона человеком — в словарь «текст → тон»; `None` снимает правку."""
        project = self.get(project_id)
        codeframe = self._find_codeframe(project, codeframe_id)
        if not codeframe.get("sentiment"):
            raise InvalidUploadError("В этом кодификаторе тональность выключена.")
        if tone is not None and tone not in TONE_VALUES:
            raise InvalidUploadError("Неизвестная тональность.")
        coding = self.coding(project_id, codeframe)
        tones = coding.setdefault("tones", {})
        key = normalize_answer(key)
        if tone is None:
            tones.pop(key, None)
        else:
            tones[key] = tone
        codeframe["coding_ref"] = store_coding(self.root / str(project_id), coding)
        self._sync_codeframe(project_id, project, codeframe)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def save_coding_result(
        self,
        project_id: UUID,
        codeframe_id: UUID,
        themes: list[dict],
        next_number: int,
        coding: dict,
    ) -> dict:
        """Итог фоновой задачи кодирования — одной ревизией."""
        project = self.get(project_id)
        codeframe = self._find_codeframe(project, codeframe_id)
        candidate = {**codeframe, "themes": themes, "next_number": next_number}
        try:
            validate_codeframe(candidate)
        except CodeframeError as exc:
            raise InvalidUploadError(str(exc)) from exc
        candidate["coding_ref"] = store_coding(self.root / str(project_id), coding)
        codeframe.update(candidate)
        self._sync_codeframe(project_id, project, codeframe)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def import_codeframe(self, project_id: UUID, codeframe_id: UUID, definition: dict) -> dict:
        """Справочник и словарь правок из файла другого проекта.

        Коды сопоставляются по названию: совпавшие остаются, новые
        добавляются. Словарь переносится для тех ответов, что встретятся.
        """
        project = self.get(project_id)
        codeframe = self._find_codeframe(project, codeframe_id)
        themes = [dict(theme) for theme in codeframe["themes"]]
        by_name = {theme["name"].casefold(): theme for theme in themes}
        ids: dict[str, str] = {}
        next_number = codeframe.get("next_number", 1)
        added = []
        for theme in definition.get("themes") or []:
            name = str(theme.get("name") or "").strip()
            if not name:
                continue
            existing = by_name.get(name.casefold())
            if existing is not None:
                ids[str(theme.get("id"))] = existing["id"]
                continue
            record = {
                "id": str(uuid4()),
                "number": next_number,
                "name": name,
                "parent_id": theme.get("parent_id"),
                "description": str(theme.get("description") or ""),
            }
            ids[str(theme.get("id"))] = record["id"]
            next_number += 1
            themes.append(record)
            added.append(record)
            by_name[name.casefold()] = record
        for record in added:
            parent = record.get("parent_id")
            record["parent_id"] = ids.get(str(parent)) if parent else None
        coding = self.coding(project_id, codeframe)
        for key, codes in (definition.get("dictionary") or {}).items():
            mapped = [ids[str(code)] for code in codes if str(code) in ids]
            coding["dictionary"][normalize_answer(key)] = mapped
        tones = coding.setdefault("tones", {})
        for key, tone in (definition.get("tones") or {}).items():
            if tone in TONE_VALUES:
                tones[normalize_answer(key)] = tone
        return self.save_coding_result(project_id, codeframe_id, themes, next_number, coding)

    def delete_codeframe(self, project_id: UUID, codeframe_id: UUID) -> dict:
        project = self.get(project_id)
        codeframe = self._find_codeframe(project, codeframe_id)
        configuration = project["configuration"]
        ensure_not_referenced(configuration, "question", codeframe["code"], "Кодификатор")
        ensure_not_referenced(
            configuration, "question", tone_variable(codeframe), "Тональность кодификатора"
        )
        configuration["codeframes"] = [
            item for item in configuration["codeframes"] if item["id"] != codeframe["id"]
        ]
        project["inspection"]["variables"] = [
            item for item in project["inspection"]["variables"]
            if item.get("codeframe_id") != codeframe["id"]
        ]
        configuration["questions"] = [
            item for item in configuration["questions"]
            if item.get("codeframe_id") != codeframe["id"]
        ]
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    @staticmethod
    def _find_codeframe(project: dict, codeframe_id: UUID | str) -> dict:
        codeframe = next(
            (
                item
                for item in project["configuration"].get("codeframes", [])
                if item["id"] == str(codeframe_id)
            ),
            None,
        )
        if codeframe is None:
            raise ProjectNotFoundError(str(codeframe_id))
        return codeframe

    def _sync_codeframe(self, project_id: UUID, project: dict, codeframe: dict) -> None:
        """Производные переменные тем и вопрос multiple-response — по кодификатору.

        Тональность — свой вопрос single choice с тем же `codeframe_id` и
        пометкой `codeframe_tone`; он живёт, пока тональность включена.
        """
        configuration = project["configuration"]
        identifier = codeframe["id"]
        variables = [
            item for item in project["inspection"]["variables"]
            if item.get("codeframe_id") != identifier
        ]
        owned = [
            item for item in configuration["questions"] if item.get("codeframe_id") == identifier
        ]
        question = next((item for item in owned if not item.get("codeframe_tone")), None)
        tone_question = next((item for item in owned if item.get("codeframe_tone")), None)
        if tone_question is not None and not codeframe.get("sentiment"):
            ensure_not_referenced(
                configuration, "question", tone_question["code"], "Тональность кодификатора"
            )
            configuration["questions"].remove(tone_question)
            tone_question = None
        if not codeframe["themes"] and not codeframe.get("sentiment"):
            project["inspection"]["variables"] = variables
            if question is not None:
                ensure_not_referenced(configuration, "question", question["code"], "Кодификатор")
                configuration["questions"].remove(question)
            return
        text_variable = codeframe_text_variable(codeframe, project)
        texts = read_project_frame(
            self.source_path(project_id), project, [text_variable], all_waves=True
        )[
            text_variable
        ]
        coding = self.coding(project_id, codeframe)
        columns = codeframe_columns(texts, codeframe, coding)
        if not codeframe["themes"]:
            if question is not None:
                ensure_not_referenced(configuration, "question", question["code"], "Кодификатор")
                configuration["questions"].remove(question)
        else:
            names = []
            for theme in codeframe["themes"]:
                name = theme_variable(codeframe, theme)
                values = columns[name]
                names.append(name)
                variables.append(
                    {
                        "name": name,
                        "label": theme["name"],
                        "storage_type": "numeric",
                        "original_format": None,
                        "measurement_level": "nominal",
                        "question_type": "multiple_choice_dichotomy",
                        "role": "question",
                        "valid_count": int(values.notna().sum()),
                        "missing_count": int(values.isna().sum()),
                        "unique_count": int(values.dropna().nunique()),
                        "value_labels": [],
                        "warnings": [],
                        "codeframe_id": identifier,
                    }
                )
            answered = int(answered_mask(texts).sum())
            if question is None:
                configuration["questions"].append(
                    {
                        "code": codeframe["code"],
                        "label": codeframe["label"],
                        "question_type": "multiple_choice_dichotomy",
                        "role": "question",
                        "source_variables": names,
                        "valid_count": answered,
                        "missing_count": len(texts) - answered,
                        "included_in_report": True,
                        "recognition": "manual",
                        "warnings": [],
                        "items": [],
                        "special_values": [],
                        "special_items": [],
                        "multiple_response": {"encoding": "dichotomy", "counted_value": 1},
                        "codeframe_id": identifier,
                    }
                )
            else:
                question.update(
                    label=codeframe["label"],
                    source_variables=names,
                    valid_count=answered,
                    missing_count=len(texts) - answered,
                )
                if question.get("nets"):
                    question["nets"] = [
                        {**net, "values": [value for value in net["values"] if value in names]}
                        for net in question["nets"]
                        if any(value in names for value in net["values"])
                    ]
        # Тональность — после вопроса кодов, чтобы в анкете стоять за ним.
        if codeframe.get("sentiment"):
            self._sync_tone(
                project, codeframe, columns[tone_variable(codeframe)], tone_question, variables
            )
        project["inspection"]["variables"] = variables

    @staticmethod
    def _sync_tone(
        project: dict,
        codeframe: dict,
        values: pd.Series,
        question: dict | None,
        variables: list[dict],
    ) -> None:
        configuration = project["configuration"]
        name = tone_variable(codeframe)
        source = next(
            (
                item
                for item in configuration["questions"]
                if item["code"] == codeframe["question_code"]
            ),
            None,
        )
        label = f"Тональность: {source['label'] if source else codeframe['question_code']}"
        valid = int(values.notna().sum())
        variables.append(
            {
                "name": name,
                "label": label,
                "storage_type": "numeric",
                "original_format": None,
                "measurement_level": "nominal",
                "question_type": "single_choice",
                "role": "question",
                "valid_count": valid,
                "missing_count": int(values.isna().sum()),
                "unique_count": int(values.dropna().nunique()),
                "value_labels": [
                    {"value": float(value), "label": tone_label}
                    for _key, value, tone_label in TONES
                ],
                "warnings": [],
                "codeframe_id": codeframe["id"],
            }
        )
        if question is None:
            configuration["questions"].append(
                {
                    "code": name,
                    "label": label,
                    "question_type": "single_choice",
                    "role": "question",
                    "source_variables": [name],
                    "valid_count": valid,
                    "missing_count": len(values) - valid,
                    "included_in_report": True,
                    "recognition": "manual",
                    "warnings": [],
                    "items": [],
                    "special_values": [],
                    "special_items": [],
                    "codeframe_id": codeframe["id"],
                    "codeframe_tone": True,
                }
            )
        else:
            question.update(valid_count=valid, missing_count=len(values) - valid)

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
        for book_name, settings in book_settings(configuration):
            if settings.get("weight_variable") == name:
                raise ConfigurationIntegrityError(
                    f"Формула выбрана весом книги «{book_name}». "
                    "Сначала смените вес в настройках этой книги."
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


def _rebuilt_themes(codeframe: dict, themes: list[dict]) -> tuple[list[dict], int]:
    """Справочник с экрана: постоянные id и номера у новых кодов.

    Новый код приходит с временным id экрана; номер у кода не
    переиспользуется после удаления — имя столбца стабильно.
    """
    existing = {theme["id"]: theme for theme in codeframe["themes"]}
    identifiers = {
        str(theme.get("id")): (theme["id"] if theme.get("id") in existing else str(uuid4()))
        for theme in themes
    }
    next_number = codeframe.get("next_number", 1)
    rebuilt = []
    for theme in themes:
        identifier = identifiers[str(theme.get("id"))]
        previous = existing.get(identifier)
        if previous is None:
            number = next_number
            next_number += 1
        else:
            number = previous["number"]
        parent = theme.get("parent_id")
        rebuilt.append(
            {
                "id": identifier,
                "number": number,
                "name": theme["name"].strip(),
                "parent_id": identifiers.get(str(parent)) if parent else None,
                "description": str(theme.get("description") or "").strip(),
            }
        )
    return rebuilt, next_number
