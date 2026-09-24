from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from .store import (
    ANALYSIS_QUESTION_TYPES,
    InvalidUploadError,
    ProjectNotFoundError,
    ProjectStore,
)


class AnalysisItems(ProjectStore):
    """Карточки и модели раздела «Анализ»."""

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

    def add_analysis_model(self, project_id: UUID, definition: dict) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        for source in (definition["dependent"], *definition["predictors"]):
            self._require_analysis_source(configuration, source)
        configuration.setdefault("analysis_models", []).append(
            {"id": str(uuid4()), **definition}
        )
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def delete_analysis_model(self, project_id: UUID, model_id: UUID) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        models = configuration.get("analysis_models", [])
        kept = [item for item in models if item["id"] != str(model_id)]
        if len(kept) == len(models):
            raise ProjectNotFoundError(str(model_id))
        configuration["analysis_models"] = kept
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    @staticmethod
    def _require_analysis_source(configuration: dict, source: dict) -> None:
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
                    f"{question['code']}: в модель входят одиночный выбор, шкала "
                    "и числовой вопрос."
                )
        elif not any(item["id"] == source["ref"] for item in configuration["recodings"]):
            raise ProjectNotFoundError(source["ref"])
