from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

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
    CodeframeError,
    answered_mask,
    codeframe_columns,
    codeframe_text_variable,
    theme_variable,
    validate_codeframe,
)
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
        configuration = project["configuration"]
        question = self._find_question(project, question_code)
        if question["question_type"] != "open_text" or len(question["source_variables"]) != 1:
            raise InvalidUploadError("Кодификатор строится для открытого вопроса.")
        codeframes = configuration.setdefault("codeframes", [])
        if any(item["question_code"] == question_code for item in codeframes):
            raise InvalidUploadError("У этого вопроса уже есть кодификатор.")
        taken = {item["name"].lower() for item in project["inspection"]["variables"]} | {
            item["code"].lower() for item in configuration["questions"]
        }
        base = re.sub(r"[^A-Za-z0-9_]", "_", question_code)
        if not re.match(r"^[A-Za-z]", base):
            base = f"T_{base}"
        code = f"{base}_T"
        index = 2
        while code.lower() in taken or any(
            name.lower().startswith(f"{code.lower()}_") for name in taken
        ):
            code = f"{base}_T{index}"
            index += 1
        codeframes.append(
            {
                "id": str(uuid4()),
                "question_code": question_code,
                "code": code,
                "label": f"Темы: {question['label']}",
                "themes": [],
                "next_number": 1,
            }
        )
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def update_codeframe(
        self, project_id: UUID, codeframe_id: UUID, label: str, themes: list[dict]
    ) -> dict:
        project = self.get(project_id)
        codeframe = self._find_codeframe(project, codeframe_id)
        existing = {theme["id"]: theme for theme in codeframe["themes"]}
        identifiers = {}
        for theme in themes:
            if theme.get("id") in existing:
                identifiers[theme["id"]] = theme["id"]
            else:
                identifiers[theme.get("id") or str(uuid4())] = str(uuid4())
        rebuilt = []
        next_number = codeframe.get("next_number", 1)
        for theme in themes:
            identifier = identifiers[theme.get("id")] if theme.get("id") in identifiers else None
            if identifier is None:
                identifier = str(uuid4())
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
                    "parent_id": identifiers.get(parent) if parent else None,
                    "queries": [line.strip() for line in theme.get("queries", []) if line.strip()],
                    "manual": previous.get("manual", {}) if previous else {},
                }
            )
        candidate = {**codeframe, "label": label, "themes": rebuilt, "next_number": next_number}
        try:
            validate_codeframe(candidate)
        except CodeframeError as exc:
            raise InvalidUploadError(str(exc)) from exc
        codeframe.update(candidate)
        self._sync_codeframe(project_id, project, codeframe)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def mark_codeframe_answer(
        self, project_id: UUID, codeframe_id: UUID, theme_id: str, row: int, value: bool | None
    ) -> dict:
        project = self.get(project_id)
        codeframe = self._find_codeframe(project, codeframe_id)
        theme = next((item for item in codeframe["themes"] if item["id"] == theme_id), None)
        if theme is None:
            raise ProjectNotFoundError(theme_id)
        manual = theme.setdefault("manual", {})
        if value is None:
            manual.pop(str(row), None)
        else:
            manual[str(row)] = 1 if value else 0
        self._sync_codeframe(project_id, project, codeframe)
        project["configuration"]["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def delete_codeframe(self, project_id: UUID, codeframe_id: UUID) -> dict:
        project = self.get(project_id)
        codeframe = self._find_codeframe(project, codeframe_id)
        configuration = project["configuration"]
        ensure_not_referenced(configuration, "question", codeframe["code"], "Кодификатор")
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
        """Производные переменные тем и вопрос multiple-response — по кодификатору."""
        configuration = project["configuration"]
        identifier = codeframe["id"]
        variables = [
            item for item in project["inspection"]["variables"]
            if item.get("codeframe_id") != identifier
        ]
        question = next(
            (item for item in configuration["questions"] if item.get("codeframe_id") == identifier),
            None,
        )
        if not codeframe["themes"]:
            project["inspection"]["variables"] = variables
            if question is not None:
                ensure_not_referenced(configuration, "question", question["code"], "Кодификатор")
                configuration["questions"].remove(question)
            return
        text_variable = codeframe_text_variable(codeframe, project)
        texts = read_project_frame(self.source_path(project_id), project, [text_variable])[
            text_variable
        ]
        columns = codeframe_columns(texts, codeframe)
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
        project["inspection"]["variables"] = variables
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
