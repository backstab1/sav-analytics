"""JSON анкеты опросной платформы как источник метаданных массива.

Выгрузка в Excel теряет метаданные SPSS: подписи кодов, текст вопроса у
пунктов матриц и multiple, шкалы измерения. В SAV они бывают неполными.
Структурный JSON анкеты (вопросы с номерами, типами и списками ответов)
восстанавливает их без догадок: имя переменной выводится из номера вопроса
по тем же правилам, по которым платформа называет столбцы выгрузки.

- одиночный выбор и выпадающий список: `Q<номер>`, коды — список ответов;
- множественный выбор: `Q<номер>_<код>` — дихотомия 0/1;
- таблица одиночного выбора: `Q<номер>_<код строки>`, коды — список ответов;
- числовой вопрос: `Q<номер>`, шкала;
- ответ с открытым полем: `Q<номер>_<код>T` — текст.

Модуль только строит описание переменных. Как его наложить на таблицу или
SAV, решает `tabular_import`.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_SCHEMA_BYTES = 30 * 1024 * 1024
SCHEMA_FILE = "survey_schema.json"
SCHEMA_EXTENSIONS = (".json",)

#: Предел подписи переменной в SAV: длиннее `write_sav` обрезает молча.
MAX_LABEL_BYTES = 256
#: Подписи дихотомии — как в SAV, который выгружает сама платформа.
DICHOTOMY_LABELS = {0.0: "НЕ ВЫБРАН", 1.0: "ВЫБРАН"}
_SINGLE = {"SingleChoice", "Dropdown_SingleChoice"}
_MULTIPLE = {"MultipleChoice", "Dropdown_MultipleChoice"}
_TABLE_SINGLE = {"Table_SingleChoice"}
_NUMERIC = {"Numeric"}
_TEXT = {"Text", "OpenText", "TextInput"}
_NO_DATA = {"Information"}
_BREAK = re.compile(r"<\s*(br|/p|/div|/li|/h\d)\b[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


class SurveySchemaError(ValueError):
    pass


@dataclass
class VariableSpec:
    name: str
    label: str
    #: single, multiple, matrix, numeric, text
    kind: str
    question: str
    value_labels: dict[float, str] = field(default_factory=dict)
    #: Текст пункта (строки матрицы, варианта multiple) без текста вопроса.
    item: str = ""

    @property
    def measure(self) -> str:
        return "scale" if self.kind == "numeric" else "nominal"

    @property
    def sav_label(self) -> str:
        """Подпись, которая помещается в SAV.

        Сокращается текст вопроса, а пункт остаётся целым: иначе у строк
        матрицы с длинным вопросом подписи совпали бы. Полный текст проект
        хранит в правках подписей (`label_overrides`).
        """
        if len(self.label.encode("utf-8")) <= MAX_LABEL_BYTES:
            return self.label
        tail = f"… {self.item}" if self.item else "…"
        room = MAX_LABEL_BYTES - len(tail.encode("utf-8"))
        head = self.label[: len(self.label) - len(self.item)].rstrip() if self.item else self.label
        while head and len(head.encode("utf-8")) > room:
            head = head[:-1]
        fitted = (head.rstrip() + tail) if head else self.item
        while len(fitted.encode("utf-8")) > MAX_LABEL_BYTES:
            fitted = fitted[:-1]
        return fitted


@dataclass
class SurveySchema:
    title: str
    variables: dict[str, VariableSpec]
    #: Текст вопроса по префиксу `Q<номер>`: подпись для столбцов, правило
    #: имени которых модулю неизвестно.
    question_texts: dict[str, str]

    def lookup(self, name: str) -> VariableSpec | None:
        if name in self.variables:
            return self.variables[name]
        for prefix, text in self.question_texts.items():
            if name.startswith(prefix + "_"):
                return VariableSpec(name=name, label=text, kind="unknown", question=prefix)
        return None


def parse_schema(data: bytes) -> SurveySchema:
    """Описание переменных из JSON анкеты; непонятный файл — ошибка."""
    if not data:
        raise SurveySchemaError("Файл анкеты пуст.")
    if len(data) > MAX_SCHEMA_BYTES:
        raise SurveySchemaError("Файл анкеты больше 30 МБ.")
    try:
        document = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SurveySchemaError("Анкета JSON не читается: файл повреждён.") from exc
    if not isinstance(document, dict) or not isinstance(document.get("Questions"), list):
        raise SurveySchemaError(
            "В JSON нет списка вопросов анкеты (Questions): это не выгрузка анкеты."
        )
    lists = {
        str(item.get("Name")): item.get("AnswerItems") or []
        for item in document.get("AnswerLists") or []
        if isinstance(item, dict)
    }
    separator = str(document.get("ColumnCodeTemplateSeparator") or "_")
    variables: dict[str, VariableSpec] = {}
    texts: dict[str, str] = {}
    for question in document["Questions"]:
        if not isinstance(question, dict) or question.get("Number") is None:
            continue
        kind = str(question.get("QuestionType") or "")
        if kind in _NO_DATA:
            continue
        base = f"Q{question['Number']}"
        text = clean_text(question.get("Text"))
        texts[base] = text or base
        answers = _items(lists.get(str(question.get("AnswerList"))))
        codes = {code: label for code, label, _ in answers}

        def add(
            name: str,
            label: str,
            spec_kind: str,
            values: dict[float, str],
            base: str = base,
            text: str = text,
        ) -> None:
            variables[name] = VariableSpec(
                name=name,
                label=label or base,
                kind=spec_kind,
                question=base,
                value_labels=dict(values),
                item=label[len(text):].strip() if label.startswith(text) else "",
            )

        if kind in _SINGLE:
            add(base, text, "single", codes)
            for code, label, open_text in answers:
                if open_text:
                    add(f"{base}{separator}{_code(code)}T", _join(text, label), "text", {})
        elif kind in _MULTIPLE:
            for code, label, open_text in answers:
                add(f"{base}{separator}{_code(code)}", _join(text, label), "multiple",
                    DICHOTOMY_LABELS)
                if open_text:
                    add(f"{base}{separator}{_code(code)}T", _join(text, label), "text", {})
        elif kind in _TABLE_SINGLE:
            for row, label, open_text in _items(lists.get(str(question.get("RowList")))):
                name = f"{base}{separator}{_code(row)}"
                add(name, _join(text, label), "matrix", codes)
                if open_text:
                    add(f"{name}T", _join(text, label), "text", {})
        elif kind in _NUMERIC:
            add(base, text, "numeric", {})
        elif kind in _TEXT:
            add(base, text, "text", {})
        elif codes:
            # Тип, которого модуль не знает: подпись вопроса и коды, если
            # переменная названа номером вопроса.
            add(base, text, "single", codes)
    if not variables:
        raise SurveySchemaError("В анкете JSON не нашлось вопросов с данными.")
    return SurveySchema(
        title=clean_text(document.get("Name")), variables=variables, question_texts=texts
    )


def label_overrides(
    variables: list[dict[str, Any]], schema: SurveySchema, *, authoritative: bool
) -> dict[str, dict[str, Any]]:
    """Полные подписи из анкеты там, где SAV хранит сокращённые.

    `authoritative` — подписи в SAV записаны по анкете (таблица → SAV), и
    любое расхождение — сокращение. В загруженном SAV подпись своя, и анкета
    поправляет её, только если та — начало текста анкеты.
    """

    def wanted(current: str, full: str) -> bool:
        return completes(current, full, authoritative=authoritative)

    overrides: dict[str, dict[str, Any]] = {}
    for variable in variables:
        spec = schema.lookup(variable["name"])
        if spec is None:
            continue
        entry: dict[str, Any] = {}
        if wanted(variable.get("label") or "", spec.label):
            entry["label"] = spec.label
        values = []
        for item in variable.get("value_labels") or []:
            try:
                full = spec.value_labels.get(float(item["value"]), "")
            except (TypeError, ValueError):
                continue
            if wanted(str(item["label"]), full):
                values.append({"value": item["value"], "label": full})
        if values:
            entry["values"] = values
        if entry:
            overrides[variable["name"]] = entry
    return overrides


def completes(current: str, full: str, *, authoritative: bool) -> bool:
    """Заменить ли подпись `current` текстом анкеты `full`."""
    if not full or current == full:
        return False
    stem = current.rstrip(" .…")
    return authoritative or (bool(stem) and full.startswith(stem))


def clean_text(value: Any) -> str:
    """Текст без разметки: переносы становятся пробелами, остальные теги
    убираются без следа — `<u>слово</u>,` не должно стать «слово ,»."""
    if not isinstance(value, str):
        return ""
    text = _TAG.sub("", _BREAK.sub(" ", value))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _items(items: Any) -> list[tuple[float, str, bool]]:
    result = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            code = float(item.get("Code"))
        except (TypeError, ValueError):
            continue
        flags = item.get("ExportedFlags") or {}
        result.append(
            (code, clean_text(item.get("Text")), bool(flags.get("OpenValueTxt")))
        )
    return result


def _code(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _join(question: str, item: str) -> str:
    return f"{question} {item}".strip() if item else question


__all__ = [
    "DICHOTOMY_LABELS",
    "SCHEMA_EXTENSIONS",
    "SCHEMA_FILE",
    "SurveySchema",
    "SurveySchemaError",
    "VariableSpec",
    "clean_text",
    "completes",
    "label_overrides",
    "parse_schema",
]
