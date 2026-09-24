from __future__ import annotations

import copy
from datetime import UTC, datetime
from uuid import UUID

import pandas as pd
import pyreadstat

from ..core.configuration_integrity import (
    ensure_not_referenced,
)
from ..core.formulas import (
    read_project_frame,
)
from ..core.not_applicable import NotApplicableConfirmationRequired, assess_not_applicable
from ..core.question_groups import QuestionGroupError, build_group, split_group
from ..core.ranking import RankingError, ranking_items
from ..core.review import CONFIRMED_RECOGNITIONS
from ..core.sav_reader import spss_missing_mask
from .store import (
    InvalidUploadError,
    ProjectNotFoundError,
    ProjectStore,
)


class QuestionEditing(ProjectStore):
    """Правка вопросов структуры: свойства, роли, «Не применимо», порядок
    и группы.
    """

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
            if changes.get("nets") and source["question_type"].startswith("multiple_choice"):
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
        confirm_substantive = bool(changes.pop("confirm_substantive", False))
        final_role = changes.get("role", question["role"])
        final_type = changes.get("question_type", question["question_type"])
        sources = changes.get("source_variables", question["source_variables"])
        if final_type == "multiple_choice_categorical" and len(sources or []) < 2:
            raise InvalidUploadError(
                "Категориальный multiple собирается из двух и более переменных-слотов."
            )
        if final_type == "ranking":
            self._apply_ranking_changes(project_id, project, question, changes, sources)
        if changes.get("not_applicable_values") and final_type == "multiple_choice_dichotomy":
            # У дихотомии выбор описывается counted_value, а не распределением
            # значений, поэтому пометка кода здесь ничего бы не изменила.
            raise InvalidUploadError(
                "Для multiple-response пропуск задаётся кодом выбранного ответа, "
                "а не пометкой «не применимо»."
            )
        self._check_role_change(project, question, changes, final_role, final_type)
        special_metric = changes.get("special_metric", question.get("special_metric", "none"))
        if special_metric in {"nps", "csat"}:
            self._check_special_metric(project_id, project, question, final_type, special_metric)
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

    def _apply_ranking_changes(
        self, project_id: UUID, project: dict, question: dict, changes: dict, sources: list[str]
    ) -> None:
        """Ранжирование проверяется по данным: базу и пропуски даёт разбор мест."""
        if changes.get("nets"):
            raise InvalidUploadError("NET-группы для ранжирования не поддерживаются.")
        candidate = {**question, **changes}
        frame = read_project_frame(self.source_path(project_id), project, sources)
        variables = {item["name"]: item for item in project["inspection"]["variables"]}
        try:
            items = ranking_items(frame, candidate, variables)
        except RankingError as exc:
            raise InvalidUploadError(str(exc)) from exc
        changes["valid_count"] = int(items[0]["ranks"].notna().sum())
        changes["missing_count"] = len(frame) - changes["valid_count"]
        changes["special_values"] = []
        changes["nets"] = []
        if question["question_type"] != "ranking":
            changes.setdefault("output_metrics", [])

    @staticmethod
    def _check_role_change(
        project: dict, question: dict, changes: dict, final_role: str, final_type: str
    ) -> None:
        """Волна и вес — служебные роли: у них свои условия, и в отчёт они не идут."""
        code = question["code"]
        questions = project["configuration"]["questions"]
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

    def _check_special_metric(
        self,
        project_id: UUID,
        project: dict,
        question: dict,
        final_type: str,
        special_metric: str,
    ) -> None:
        """NPS и CSAT фиксируют границы групп, поэтому шкала должна в них укладываться."""
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

    def group_questions(
        self,
        project_id: UUID,
        codes: list[str],
        question_type: str,
        *,
        code: str | None = None,
        label: str | None = None,
        ranking_encoding: str | None = None,
    ) -> dict:
        """Собрать выбранные одиночные вопросы в multiple, матрицу или ранжирование.

        Вопрос, на который ссылается баннер, фильтр, перекодировка или база,
        в группу не уходит: после сборки его кода не станет, и ссылка повисла
        бы. Связи снимаются сначала, это же правило действует при удалении.
        """
        project = self.get(project_id)
        configuration = project["configuration"]
        for member in dict.fromkeys(codes):
            ensure_not_referenced(configuration, "question", member, f"Вопрос {member}")
        variables = {item["name"]: item for item in project["inspection"]["variables"]}
        try:
            group = build_group(
                configuration["questions"], variables, codes, question_type, code=code, label=label,
                ranking_encoding=ranking_encoding,
            )
        except QuestionGroupError as exc:
            raise InvalidUploadError(str(exc)) from exc
        frame = read_project_frame(self.source_path(project_id), project, group["source_variables"])
        answered = frame[group["source_variables"]].notna().any(axis=1)
        if question_type == "ranking":
            try:
                answered = ranking_items(frame, group, variables)[0]["ranks"].notna()
            except RankingError as exc:
                raise InvalidUploadError(str(exc)) from exc
        group["valid_count"] = int(answered.sum())
        group["missing_count"] = int((~answered).sum())
        members = set(codes)
        questions = configuration["questions"]
        position = next(index for index, item in enumerate(questions) if item["code"] in members)
        remaining = [item for item in questions if item["code"] not in members]
        remaining.insert(min(position, len(remaining)), group)
        configuration["questions"] = remaining
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project

    def ungroup_question(self, project_id: UUID, code: str) -> dict:
        """Разобрать группу на одиночные вопросы — по одному на переменную."""
        project = self.get(project_id)
        configuration = project["configuration"]
        group = self._find_question(project, code)
        ensure_not_referenced(configuration, "question", code, f"Вопрос {code}")
        variables = {item["name"]: item for item in project["inspection"]["variables"]}
        try:
            singles = split_group(group, variables)
        except QuestionGroupError as exc:
            raise InvalidUploadError(str(exc)) from exc
        taken = {item["code"] for item in configuration["questions"] if item["code"] != code}
        clashes = [item["code"] for item in singles if item["code"] in taken]
        if clashes:
            raise InvalidUploadError(
                "Коды уже заняты другими вопросами: " + ", ".join(clashes) + "."
            )
        questions = configuration["questions"]
        position = questions.index(group)
        configuration["questions"] = questions[:position] + singles + questions[position + 1 :]
        configuration["updated_at"] = datetime.now(UTC).isoformat()
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
    from ..core.report_settings import NUMERIC_METRICS, SCALE_METRICS

    allowed = {
        "ranking": ("distribution", "mean"),
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
