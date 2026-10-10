"""Планы ассистента: фиксированный набор шагов, проверка, описание, применение.

Модель не меняет проект. Она присылает план — список шагов из набора ниже.
Бэкенд прогоняет его в черновике проекта (`ProjectStore.draft`) через те же
методы репозитория и те же проверки, что обычные эндпоинты, и собирает
описание шагов сам, по уже выполненному черновику. Карточка показывает это
описание, а не текст модели, поэтому «что написано» и «что будет сделано»
не расходятся. Применяет план только интерфейс по нажатию пользователя:
все шаги — одной записью проекта, одним шагом отмены.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ..api_schemas import (
    BannerBlock,
    BannerDefinition,
    CategoricalRecodeDefinition,
    ConditionalRecodeDefinition,
    FilterDefinition,
    FilterGroup,
    FormulaDefinition,
    NetDefinition,
    NumericRecodeDefinition,
    TableColumnBlock,
    TableReportLayout,
)
from ..core.banner import validate_banner
from ..core.configuration_integrity import validate_configuration_references
from ..core.filtering import validate_filter
from ..core.recoding import validate_recode
from .catalog import (
    can_be_column,
    can_be_row,
    question_by_code,
    recoding_by_ref,
    table_report,
    value_labels,
)

MAX_STEPS = 20
_STEP_REF = re.compile(r"^\$step:(\d+)$")
_LAYOUT_KEYS = tuple(TableReportLayout.model_fields)


class PlanError(ValueError):
    """План нельзя выполнить: текст уходит модели, чтобы она исправила план."""


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _TableStep(_Step):
    # id таблицы или «$step:N» созданной планом; пусто — открытая таблица.
    table: str | None = None


class SetRows(_TableStep):
    op: Literal["table.set_rows"]
    rows: list[str] = Field(min_length=1, max_length=50)


class AddRows(_TableStep):
    op: Literal["table.add_rows"]
    codes: list[str] = Field(min_length=1, max_length=50)


class RemoveRows(_TableStep):
    op: Literal["table.remove_rows"]
    codes: list[str] = Field(min_length=1, max_length=50)


class SetColumns(_TableStep):
    op: Literal["table.set_columns"]
    cols: list[TableColumnBlock] = Field(min_length=1, max_length=20)


class UseBanner(_TableStep):
    op: Literal["table.use_banner"]
    banner_id: str


class ClearColumns(_TableStep):
    op: Literal["table.clear_columns"]


class SetFilter(_TableStep):
    op: Literal["table.set_filter"]
    filter_id: str | None = None


class SetBase(_TableStep):
    op: Literal["table.set_base"]
    base: Literal["main", "filter"]


class SetMeasure(_TableStep):
    op: Literal["table.set_measure"]
    measure: Literal["value", "counts", "index"]


class SetScaleBox(_TableStep):
    op: Literal["table.set_scale_box"]
    scale_box: int | None = Field(default=None, ge=1, le=3)


class SetTableNets(_TableStep):
    op: Literal["table.set_nets"]
    code: str
    nets: list[NetDefinition] = Field(default_factory=list, max_length=20)


class CreateTable(_Step):
    op: Literal["table.create"]
    name: str | None = Field(default=None, min_length=1, max_length=120)
    rows: list[str] = Field(default_factory=list, max_length=50)
    cols: list[TableColumnBlock] = Field(default_factory=list, max_length=20)
    banner_id: str | None = None
    filter_id: str | None = None
    base: Literal["main", "filter"] = "main"
    measure: Literal["value", "counts", "index"] = "value"
    scale_box: int | None = Field(default=None, ge=1, le=3)


class CopyTable(_TableStep):
    op: Literal["table.copy"]


class RenameTable(_TableStep):
    op: Literal["table.rename"]
    name: str = Field(min_length=1, max_length=120)


class CreateRecoding(_Step):
    op: Literal["recoding.create"]
    definition: dict[str, Any]


class CreateFilter(_Step):
    op: Literal["filter.create"]
    name: str = Field(min_length=1, max_length=500)
    rule: FilterGroup


class CreateBanner(_Step):
    op: Literal["banner.create"]
    name: str = Field(min_length=1, max_length=500)
    blocks: list[BannerBlock] = Field(min_length=1, max_length=50)


class SetQuestionNets(_Step):
    op: Literal["question.set_nets"]
    code: str
    nets: list[NetDefinition] = Field(default_factory=list, max_length=20)


class CreateFormula(_Step):
    op: Literal["formula.create"]
    name: str
    label: str
    expression: str


Step = Annotated[
    SetRows
    | AddRows
    | RemoveRows
    | SetColumns
    | UseBanner
    | ClearColumns
    | SetFilter
    | SetBase
    | SetMeasure
    | SetScaleBox
    | SetTableNets
    | CreateTable
    | CopyTable
    | RenameTable
    | CreateRecoding
    | CreateFilter
    | CreateBanner
    | SetQuestionNets
    | CreateFormula,
    Field(discriminator="op"),
]
_STEP = TypeAdapter(Step)
OPERATIONS: list[str] = [
    get_args(member.model_fields["op"].annotation)[0] for member in get_args(get_args(Step)[0])
]
_RECODE_SCHEMAS: dict[str, type[BaseModel]] = {
    "ranges": NumericRecodeDefinition,
    "categories": CategoricalRecodeDefinition,
    "conditions": ConditionalRecodeDefinition,
}


@dataclass
class Outcome:
    description: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _Run:
    """Выполнение шагов одного плана в черновике проекта."""

    def __init__(self, repository: Any, project_id: UUID, table_id: str | None) -> None:
        self.repository = repository
        self.project_id = project_id
        self.table_id = table_id
        self.created: dict[int, str] = {}
        self.outcome = Outcome()
        self._number = 0

    def project(self) -> dict:
        return self.repository.get(self.project_id)

    # --- ссылки --------------------------------------------------------------

    def _substitute(self, value: Any, number: int) -> Any:
        if isinstance(value, str) and (match := _STEP_REF.match(value)):
            target = int(match.group(1))
            if target >= number or target not in self.created:
                raise PlanError(
                    f"Ссылка {value}: шаг {target} должен идти раньше и что-то создавать."
                )
            return self.created[target]
        if isinstance(value, list):
            return [self._substitute(item, number) for item in value]
        if isinstance(value, dict):
            return {key: self._substitute(item, number) for key, item in value.items()}
        return value

    def _resolve_sources(self, value: Any) -> Any:
        """Источник-перекодировку можно назвать кодом: в конфигурации нужен id."""
        if isinstance(value, list):
            return [self._resolve_sources(item) for item in value]
        if not isinstance(value, dict):
            return value
        resolved = {key: self._resolve_sources(item) for key, item in value.items()}
        if resolved.get("kind") == "recoding" and isinstance(resolved.get("ref"), str):
            recoding = recoding_by_ref(self.project(), resolved["ref"])
            if recoding is None:
                raise PlanError(f"Перекодировки {resolved['ref']} нет в проекте.")
            resolved["ref"] = recoding["id"]
        return resolved

    # --- подписи для описания --------------------------------------------------

    def _question(self, code: str) -> str:
        question = question_by_code(self.project(), code)
        return f"{code} «{question['label']}»" if question else code

    def _source(self, source: dict) -> str:
        if source["kind"] == "recoding":
            recoding = recoding_by_ref(self.project(), source["ref"])
            return f"{recoding['code']} «{recoding['name']}»" if recoding else source["ref"]
        return self._question(source["ref"])

    def _blocks(self, blocks: list[dict]) -> str:
        # Блоки — через запятую, вложенная переменная — через «×» внутри блока.
        return ", ".join(
            " × ".join(map(self._source, block["sources"])) for block in blocks
        )

    def _named(self, section: str, identifier: str | None) -> str:
        item = next(
            (
                entry
                for entry in self.project()["configuration"].get(section, [])
                if entry["id"] == identifier
            ),
            None,
        )
        return f"«{item['name']}»" if item else str(identifier)

    # --- таблицы ------------------------------------------------------------------

    def _target(self, step: _TableStep) -> dict:
        identifier = step.table or self.table_id
        if identifier is None:
            raise PlanError("Таблица не открыта: создайте её шагом table.create.")
        report = table_report(self.project(), identifier)
        if report is None:
            raise PlanError(f"Таблицы {identifier} нет в проекте.")
        return report

    def _check_layout(self, layout: dict) -> None:
        project = self.project()
        for code in layout["rows"]:
            question = question_by_code(project, code)
            if question is None:
                raise PlanError(f"Вопроса {code} нет. Найдите код через list_questions.")
            if not can_be_row(question):
                raise PlanError(f"{code} нельзя поставить в строки (can_be_row = false).")
        for source in (source for block in layout["cols"] for source in block["sources"]):
            if source["kind"] == "recoding":
                continue
            question = question_by_code(project, source["ref"])
            if question is None:
                raise PlanError(f"Вопроса {source['ref']} нет. Найдите код через list_questions.")
            if not can_be_column(project, question):
                raise PlanError(
                    f"{source['ref']} нельзя поставить в колонки: сначала перекодировка "
                    "(recoding.create)."
                )

    def _write_layout(self, report: dict, change: Callable[[dict], None]) -> None:
        layout = {key: report[key] for key in _LAYOUT_KEYS if key in report}
        change(layout)
        try:
            validated = TableReportLayout.model_validate(layout).model_dump(mode="json")
        except ValidationError as exc:
            raise PlanError(_validation_text(exc)) from exc
        self._check_layout(validated)
        self.repository.update_table_report(self.project_id, UUID(report["id"]), validated)

    def _nets(self, code: str, nets: list[NetDefinition]) -> list[dict]:
        question = question_by_code(self.project(), code)
        if question is None:
            raise PlanError(f"Вопроса {code} нет.")
        known = {_normalized(item["value"]): item["value"] for item in value_labels(
            self.project(), question
        )}
        result = []
        for net in nets:
            values = []
            for value in net.values:
                if _normalized(value) not in known:
                    raise PlanError(
                        f"У {code} нет категории {value}: смотрите categories в get_question."
                    )
                values.append(known[_normalized(value)])
            result.append({"label": net.label.strip(), "values": values})
        return result

    def _table_step(self, step: _TableStep) -> None:
        report = self._target(step)
        prefix = f"Таблица «{report['name']}»: "
        text: str
        if isinstance(step, SetRows):
            rows = list(dict.fromkeys(step.rows))
            self._write_layout(report, lambda layout: layout.update(rows=rows))
            text = "строки — " + ", ".join(map(self._question, rows))
        elif isinstance(step, AddRows):
            added = [code for code in dict.fromkeys(step.codes) if code not in report["rows"]]
            self._write_layout(report, lambda layout: layout.update(rows=[*layout["rows"], *added]))
            text = "добавлю в строки " + ", ".join(map(self._question, added or step.codes))
        elif isinstance(step, RemoveRows):
            removed = set(step.codes)
            self._write_layout(
                report,
                lambda layout: layout.update(
                    rows=[code for code in layout["rows"] if code not in removed]
                ),
            )
            text = "уберу из строк " + ", ".join(map(self._question, step.codes))
        elif isinstance(step, SetColumns):
            cols = self._resolve_sources([item.model_dump() for item in step.cols])
            self._write_layout(
                report,
                lambda layout: layout.update(cols=cols, banner_id=None),
            )
            text = "колонки — " + self._blocks(cols)
        elif isinstance(step, UseBanner):
            self._write_layout(
                report,
                lambda layout: layout.update(banner_id=step.banner_id, cols=[]),
            )
            text = f"колонки — баннер {self._named('banners', step.banner_id)}"
        elif isinstance(step, ClearColumns):
            self._write_layout(report, lambda layout: layout.update(banner_id=None, cols=[]))
            text = "уберу разрез, останется только Total"
        elif isinstance(step, SetFilter):
            self._write_layout(report, lambda layout: layout.update(filter_id=step.filter_id))
            text = (
                f"фильтр {self._named('filters', step.filter_id)}"
                if step.filter_id
                else "сниму фильтр"
            )
        elif isinstance(step, SetBase):
            self._write_layout(report, lambda layout: layout.update(sheet=step.base))
            text = "доли от всей выборки" if step.base == "main" else "доли от ответивших на вопрос"
        elif isinstance(step, SetMeasure):
            self._write_layout(report, lambda layout: layout.update(measure=step.measure))
            text = "показатель — " + {
                "value": "проценты",
                "counts": "числа ответивших",
                "index": "индекс к Total",
            }[step.measure]
        elif isinstance(step, SetScaleBox):
            self._write_layout(report, lambda layout: layout.update(scale_box=step.scale_box))
            text = (
                f"топ-{step.scale_box} и боттом-{step.scale_box} для шкал"
                if step.scale_box
                else "топ-бокс по настройкам отчёта"
            )
        elif isinstance(step, SetTableNets):
            nets = self._nets(step.code, step.nets)

            def change(layout: dict) -> None:
                table_nets = dict(layout.get("nets") or {})
                if nets:
                    table_nets[step.code] = nets
                else:
                    table_nets.pop(step.code, None)
                layout["nets"] = table_nets

            self._write_layout(report, change)
            text = (
                f"NET-ы {self._question(step.code)} только в этой таблице — " + _nets_text(nets)
                if nets
                else f"уберу NET-ы таблицы у {self._question(step.code)}"
            )
        elif isinstance(step, CopyTable):
            before = {item["id"] for item in self.project()["configuration"]["table_reports"]}
            self.repository.copy_table_report(self.project_id, UUID(report["id"]))
            self.created[self._number] = _new_id(self.project(), "table_reports", before)
            text = "сделаю копию"
        elif isinstance(step, RenameTable):
            self.repository.rename_table_report(
                self.project_id, UUID(report["id"]), step.name.strip()
            )
            text = f"переименую в «{step.name.strip()}»"
        else:  # pragma: no cover - набор шагов закрыт
            raise PlanError(f"Неизвестный шаг {step.op}.")
        self.outcome.description.append(prefix + text)

    def _create_table(self, step: CreateTable) -> None:
        layout = {
            "rows": list(dict.fromkeys(step.rows)),
            "cols": self._resolve_sources([item.model_dump() for item in step.cols]),
            "banner_id": step.banner_id,
            "filter_id": step.filter_id,
            "sheet": step.base,
            "measure": step.measure,
            "scale_box": step.scale_box,
        }
        try:
            validated = TableReportLayout.model_validate(layout).model_dump(mode="json")
        except ValidationError as exc:
            raise PlanError(_validation_text(exc)) from exc
        self._check_layout(validated)
        before = {item["id"] for item in self.project()["configuration"].get("table_reports", [])}
        self.repository.create_table_report(self.project_id, step.name, validated)
        identifier = _new_id(self.project(), "table_reports", before)
        self.created[self._number] = identifier
        created = table_report(self.project(), identifier)
        assert created is not None
        name = created["name"]
        parts = []
        if validated["rows"]:
            parts.append("строки — " + ", ".join(map(self._question, validated["rows"])))
        if validated["cols"]:
            parts.append("колонки — " + self._blocks(validated["cols"]))
        self.outcome.description.append(
            f"Создам таблицу «{name}»" + (": " + "; ".join(parts) if parts else "")
        )

    # --- объекты проекта -----------------------------------------------------------

    def _create_recoding(self, step: CreateRecoding) -> None:
        definition = dict(step.definition)
        mode = definition.get("mode", "ranges")
        schema = _RECODE_SCHEMAS.get(mode)
        if schema is None:
            raise PlanError("Режим перекодировки — ranges, categories или conditions.")
        definition["mode"] = mode
        try:
            payload = schema.model_validate(definition).model_dump(mode="json")
        except ValidationError as exc:
            raise PlanError(_validation_text(exc)) from exc
        payload = self._resolve_sources(payload)
        project = self.project()
        validate_recode(payload, project["inspection"]["variables"], project)
        self.repository.create_recoding(self.project_id, payload)
        recoding = recoding_by_ref(self.project(), payload["code"])
        assert recoding is not None
        self.created[self._number] = recoding["id"]
        categories = "; ".join(_category_text(item, mode) for item in payload["categories"])
        source = f" из {payload['source_variable']}" if payload.get("source_variable") else ""
        self.outcome.description.append(
            f"Создам перекодировку {payload['code']} «{payload['name']}»{source}: {categories}"
        )

    def _create_filter(self, step: CreateFilter) -> None:
        payload = FilterDefinition(name=step.name, rule=step.rule).model_dump(mode="json")
        payload = self._resolve_sources(payload)
        validate_filter(payload, self.project())
        before = {item["id"] for item in self.project()["configuration"]["filters"]}
        self.repository.create_filter(self.project_id, payload)
        self.created[self._number] = _new_id(self.project(), "filters", before)
        self.outcome.description.append(
            f"Создам фильтр «{payload['name']}»: {self._rule_text(payload['rule'])}"
        )

    def _create_banner(self, step: CreateBanner) -> None:
        try:
            payload = BannerDefinition.model_validate(
                {"name": step.name, "blocks": [block.model_dump(exclude_unset=True) for block in
                                               step.blocks]}
            ).model_dump(mode="json", exclude_unset=True)
        except ValidationError as exc:
            raise PlanError(_validation_text(exc)) from exc
        payload = self._resolve_sources(payload)
        project = self.project()
        validate_banner(payload, project)
        report_banner = project["configuration"].get("report_banner_id")
        before = {item["id"] for item in project["configuration"]["banners"]}
        self.repository.create_banner(self.project_id, payload)
        # Создание баннера через эндпоинт делает его баннером отчёта. Ассистент
        # книгу не меняет: баннер только сохраняется.
        project = self.project()
        project["configuration"]["report_banner_id"] = report_banner
        self.repository.save_project(self.project_id, project)
        self.created[self._number] = _new_id(self.project(), "banners", before)
        blocks = "; ".join(
            " × ".join(self._source(source) for source in block["sources"])
            for block in payload["blocks"]
        )
        self.outcome.description.append(f"Сохраню баннер «{payload['name']}»: {blocks}")

    def _set_question_nets(self, step: SetQuestionNets) -> None:
        nets = self._nets(step.code, step.nets)
        self.repository.update_question(self.project_id, step.code, {"nets": nets})
        self.outcome.description.append(
            f"NET-ы вопроса {self._question(step.code)} во всём проекте — " + _nets_text(nets)
            if nets
            else f"Уберу NET-ы вопроса {self._question(step.code)} во всём проекте"
        )
        self.outcome.warnings.append(
            f"Меняет книгу отчёта: NET-ы {step.code} появятся во всех таблицах и в Excel."
        )

    def _create_formula(self, step: CreateFormula) -> None:
        try:
            payload = FormulaDefinition(
                name=step.name, label=step.label, expression=step.expression
            ).model_dump()
        except ValidationError as exc:
            raise PlanError(_validation_text(exc)) from exc
        self.repository.create_formula(self.project_id, payload)
        self.created[self._number] = payload["name"]
        self.outcome.description.append(
            f"Создам вычисляемую переменную {payload['name']} «{payload['label']}» = "
            f"{payload['expression']}"
        )

    def _rule_text(self, rule: dict) -> str:
        words = {
            "eq": "=", "ne": "≠", "in": "одно из", "not_in": "ни одно из", "gt": ">",
            "lt": "<", "between": "от … до", "filled": "заполнено", "missing": "пропущено",
            "selected": "выбрано", "selected_any": "выбрано любое из",
            "selected_all": "выбраны все из", "selected_none": "не выбрано ни одно из",
        }
        parts = []
        for item in rule["items"]:
            if item.get("kind") == "group":
                parts.append(f"({self._rule_text(item)})")
                continue
            values = ", ".join(map(str, item.get("values") or []))
            if item["operator"] == "between":
                values = f"{item.get('lower')}–{item.get('upper')}"
            parts.append(
                f"{self._source(item['source'])} {words.get(item['operator'], item['operator'])}"
                + (f" {values}" if values else "")
            )
        return f" {'и' if rule.get('operator', 'and') == 'and' else 'или'} ".join(parts)

    # --- выполнение ------------------------------------------------------------------

    def execute(self, raw_steps: list[dict]) -> Outcome:
        if not raw_steps:
            raise PlanError("В плане нет шагов.")
        if len(raw_steps) > MAX_STEPS:
            raise PlanError(f"Не больше {MAX_STEPS} шагов в плане.")
        for number, raw in enumerate(raw_steps, start=1):
            self._number = number
            try:
                cleaned = {key: value for key, value in raw.items() if value is not None}
                step = _STEP.validate_python(self._substitute(cleaned, number))
                self._dispatch(step)
            except PlanError as exc:
                raise PlanError(f"Шаг {number} ({raw.get('op')}): {exc}") from exc
            except ValidationError as exc:
                raise PlanError(
                    f"Шаг {number} ({raw.get('op')}): {_validation_text(exc)}"
                ) from exc
            except (ValueError, LookupError) as exc:
                raise PlanError(f"Шаг {number} ({raw.get('op')}): {exc}") from exc
        try:
            validate_configuration_references(self.project()["configuration"])
        except ValueError as exc:
            raise PlanError(str(exc)) from exc
        return self.outcome

    def _dispatch(self, step: Any) -> None:
        if isinstance(step, CreateTable):
            self._create_table(step)
        elif isinstance(step, _TableStep):
            self._table_step(step)
        elif isinstance(step, CreateRecoding):
            self._create_recoding(step)
        elif isinstance(step, CreateFilter):
            self._create_filter(step)
        elif isinstance(step, CreateBanner):
            self._create_banner(step)
        elif isinstance(step, SetQuestionNets):
            self._set_question_nets(step)
        elif isinstance(step, CreateFormula):
            self._create_formula(step)


def dry_run(
    repository: Any, project_id: UUID, table_id: str | None, steps: list[dict]
) -> Outcome:
    """Проверить план и собрать его описание, ничего не записывая."""
    with repository.draft(project_id):
        return _Run(repository, project_id, table_id).execute(steps)


def apply(
    repository: Any, project_id: UUID, table_id: str | None, steps: list[dict], key: str
) -> tuple[dict, dict, Outcome]:
    """Выполнить план и записать результат одной ревизией.

    Возвращает проект до и после записи и описание. Ревизия проверяется при
    записи как обычно: если проект успели изменить, будет конфликт.
    """
    before = repository.get(project_id)
    with repository.draft(project_id) as current:
        outcome = _Run(repository, project_id, table_id).execute(steps)
        result = current()
    repository.save_project(project_id, result, coalesce=key)
    return before, repository.get(project_id), outcome


def _new_id(project: dict, section: str, before: set[str]) -> str:
    return next(
        item["id"]
        for item in project["configuration"].get(section, [])
        if item["id"] not in before
    )


def _normalized(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return repr(int(number)) if number.is_integer() else repr(number)


def _nets_text(nets: list[dict]) -> str:
    return "; ".join(
        f"«{net['label']}» = " + ", ".join(_short(value) for value in net["values"])
        for net in nets
    )


def _short(value: Any) -> str:
    return _normalized(value) if isinstance(value, int | float) else str(value)


def _category_text(category: dict, mode: str) -> str:
    if mode == "ranges":
        lower = "" if category.get("lower") is None else _short(category["lower"])
        upper = "" if category.get("upper") is None else _short(category["upper"])
        return f"{category['label']} ({lower}–{upper})"
    if mode == "categories":
        return f"{category['label']} = " + ", ".join(_short(v) for v in category["values"])
    return category["label"] + (" (иначе)" if category.get("otherwise") else "")


def _validation_text(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, error['loc'])) or 'значение'}: {error['msg']}"
        for error in exc.errors()[:5]
    )
