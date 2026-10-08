"""Анкета как источник подписей: текст файла, каталог массива, проверка
предложений модели и их применение.

Модель сопоставляет анкету с массивом и предлагает подписи вопросов,
переменных и кодов, тип вопроса и порядок. Здесь ничего не доверяется
модели: каждое предложение сверяется с проектом и превращается в строку
«было → станет», а строка, которую не к чему применить, отбрасывается с
причиной. Применяет выбранные строки репозиторий одной ревизией.

Подписи переменных и кодов живут в `configuration.label_overrides`, а не
только в описании структуры: описание пересобирается из SAV при
перераспознавании и при новой волне, и правки накладываются заново
(`apply_label_overrides`). К тому же конфигурация входит в ключ кэша
отчёта, а описание — нет.
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .configuration_integrity import find_references
from .question_groups import QuestionGroupError, build_group

QUESTIONNAIRE_EXTENSIONS = (".docx", ".pdf", ".txt", ".md")
MAX_QUESTIONNAIRE_BYTES = 30 * 1024 * 1024
# Сколько текста анкеты уходит модели. Анкета на 60 страниц — около 150 тысяч
# знаков; длиннее обычно приложения и шоукарты, их обрезаем с пометкой.
MAX_TEXT_CHARS = 250_000
# Коды без подписей показываются модели, только если их немного: у числовой
# переменной с сотней значений подписывать нечего.
MAX_UNLABELED_CODES = 40

# Типы, между которыми вопрос из одной переменной можно переключить по
# анкете. Группы анкета не переключает, а собирает отдельной строкой `group`
# из одиночных вопросов — той же `build_group`, что и ручная сборка.
SINGLE_VARIABLE_TYPES = ("single_choice", "scale", "numeric", "open_text")
# Группы, которые собираются по анкете. Ранжированию нужна кодировка (ранг
# по пункту или пункт по рангу), по тексту анкеты её не угадать.
QUESTIONNAIRE_GROUP_TYPES = (
    "multiple_choice_dichotomy",
    "multiple_choice_categorical",
    "matrix",
)
GROUP_TYPE_NAMES = {
    "multiple_choice_dichotomy": "multiple-дихотомия",
    "multiple_choice_categorical": "категориальный multiple",
    "matrix": "матрица",
}

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class QuestionnaireError(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> str:
    """Текст анкеты из DOCX, PDF или простого текста."""
    name = filename.lower()
    if not name.endswith(QUESTIONNAIRE_EXTENSIONS):
        raise QuestionnaireError("Анкета принимается в DOCX, PDF или TXT.")
    if not data:
        raise QuestionnaireError("Файл анкеты пуст.")
    if len(data) > MAX_QUESTIONNAIRE_BYTES:
        raise QuestionnaireError("Файл анкеты больше 30 МБ.")
    if name.endswith(".docx"):
        text = _docx_text(data)
    elif name.endswith(".pdf"):
        text = _pdf_text(data)
    else:
        text = _plain_text(data)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 20:
        raise QuestionnaireError(
            "В файле анкеты не нашлось текста. Если это скан, нужен файл с текстовым слоем."
        )
    return text


def _plain_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _docx_text(data: bytes) -> str:
    """Абзацы и таблицы документа по порядку; ячейки строки — через « | »."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise QuestionnaireError("Файл DOCX повреждён или не является документом Word.") from exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise QuestionnaireError("Файл DOCX повреждён.") from exc
    body = root.find(f"{_W}body")
    if body is None:
        return ""
    lines: list[str] = []
    for block in body:
        if block.tag == f"{_W}p":
            lines.append(_paragraph_text(block))
        elif block.tag == f"{_W}tbl":
            for row in block.iter(f"{_W}tr"):
                cells = [
                    " ".join(_paragraph_text(p) for p in cell.iter(f"{_W}p")).strip()
                    for cell in row.iter(f"{_W}tc")
                ]
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        elif node.tag in {f"{_W}br", f"{_W}cr"}:
            parts.append("\n")
    return "".join(parts)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise QuestionnaireError("PDF защищён паролем.")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise QuestionnaireError("Файл PDF повреждён.") from exc


QUESTIONNAIRE_FILE = "questionnaire.txt"
QUESTIONNAIRE_META = "questionnaire.json"


def store_questionnaire(project_dir: Path, filename: str, text: str) -> None:
    """Сохранить текст последней загруженной анкеты рядом с проектом.

    Анкета — не конфигурация: в историю отмены и ключ кэша отчёта она не
    входит. Её читает ИИ отчёт, чтобы строить разделы по блокам анкеты.
    """
    (project_dir / QUESTIONNAIRE_FILE).write_text(text, encoding="utf-8")
    meta = {"filename": filename, "chars": len(text)}
    (project_dir / QUESTIONNAIRE_META).write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def stored_questionnaire(project_dir: Path) -> dict[str, Any] | None:
    """Сохранённая анкета: имя файла, длина и текст, или None."""
    text_path = project_dir / QUESTIONNAIRE_FILE
    meta_path = project_dir / QUESTIONNAIRE_META
    if not text_path.is_file() or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {**meta, "text": text_path.read_text(encoding="utf-8")}


def trimmed_text(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS], True


def value_key(value: Any) -> str:
    """Код как строка сравнения: 1, 1.0 и "1" — один код."""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int | float):
        number = float(value)
        if math.isfinite(number) and number.is_integer():
            return str(int(number))
        return repr(number)
    text = str(value).strip()
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return text
    if math.isfinite(number) and number.is_integer():
        return str(int(number))
    return text


def catalog(project: dict, observed: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Что модель видит о массиве: вопросы по порядку, их переменные с
    подписями и коды без подписей.

    `observed` — встреченные в данных значения переменных, у которых их
    немного; по ним модель понимает, какие коды подписать.
    """
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    entries = []
    for question in project["configuration"]["questions"]:
        if question.get("formula_id") or question.get("codeframe_id"):
            continue
        members = []
        for name in question["source_variables"]:
            variable = variables.get(name) or {}
            labeled = {value_key(item["value"]) for item in variable.get("value_labels") or []}
            unlabeled = [
                value for value in observed.get(name, []) if value_key(value) not in labeled
            ]
            members.append(
                {
                    "name": name,
                    "label": variable.get("label") or "",
                    "values": [
                        {"value": item["value"], "label": item["label"]}
                        for item in variable.get("value_labels") or []
                    ],
                    "unlabeled_codes": unlabeled[:MAX_UNLABELED_CODES],
                }
            )
        entries.append(
            {
                "code": question["code"],
                "label": question["label"],
                "question_type": question["question_type"],
                "role": question["role"],
                "variables": members,
            }
        )
    return entries


def observed_candidates(project: dict) -> list[str]:
    """Переменные, у которых стоит прочитать значения из данных: без подписей
    кодов и с небольшим числом различных значений."""
    names = []
    for variable in project["inspection"]["variables"]:
        if variable.get("formula_id") or variable.get("codeframe_id"):
            continue
        if variable.get("value_labels"):
            # Подписи есть, но в данных могут встретиться и неподписанные коды.
            if variable.get("unique_count", 0) <= MAX_UNLABELED_CODES:
                names.append(variable["name"])
            continue
        if 0 < variable.get("unique_count", 0) <= MAX_UNLABELED_CODES:
            names.append(variable["name"])
    return names


def proposal_rows(
    project: dict, proposal: dict[str, Any], observed: dict[str, list[Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Предложение модели → строки «было → станет» и отброшенное с причинами.

    Строка, которая ничего не меняет, не показывается вовсе.
    """
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    questions = {item["code"]: item for item in project["configuration"]["questions"]}
    variables = {item["name"]: item for item in project["inspection"]["variables"]}

    for item in _records(proposal.get("questions")):
        code = str(item.get("code") or "")
        question = questions.get(code)
        if question is None:
            skipped.append(f"Вопроса {code or '(без кода)'} нет в массиве.")
            continue
        label = _clean_label(item.get("label"))
        if label and label != question["label"]:
            rows.append(
                {
                    "kind": "question_label",
                    "question": code,
                    "before": question["label"],
                    "after": label,
                }
            )
        new_type = item.get("question_type")
        if new_type and new_type != question["question_type"]:
            if len(question["source_variables"]) != 1 or new_type not in SINGLE_VARIABLE_TYPES:
                skipped.append(
                    f"{code}: тип «{new_type}» по анкете не ставится — группы собираются "
                    "в «Данных»."
                )
            elif question["question_type"] not in SINGLE_VARIABLE_TYPES:
                skipped.append(f"{code}: тип служебного вопроса по анкете не меняется.")
            else:
                rows.append(
                    {
                        "kind": "question_type",
                        "question": code,
                        "before": question["question_type"],
                        "after": new_type,
                    }
                )

    for item in _records(proposal.get("variables")):
        name = str(item.get("name") or "")
        variable = variables.get(name)
        if variable is None or variable.get("formula_id") or variable.get("codeframe_id"):
            skipped.append(f"Переменной {name or '(без имени)'} нет в массиве.")
            continue
        label = _clean_label(item.get("label"))
        if label and label != (variable.get("label") or ""):
            rows.append(
                {
                    "kind": "variable_label",
                    "variable": name,
                    "before": variable.get("label") or "",
                    "after": label,
                }
            )
        known = {
            value_key(entry["value"]): entry for entry in variable.get("value_labels") or []
        }
        seen = {value_key(value): value for value in observed.get(name, [])}
        for entry in _records(item.get("values")):
            key = value_key(entry.get("value"))
            value_label = _clean_label(entry.get("label"))
            if not value_label:
                continue
            if key in known:
                if known[key]["label"] == value_label:
                    continue
                value, before = known[key]["value"], known[key]["label"]
            elif key in seen:
                value, before = seen[key], ""
            else:
                skipped.append(f"{name}: кода {entry.get('value')} нет в данных.")
                continue
            rows.append(
                {
                    "kind": "value_label",
                    "variable": name,
                    "value": value,
                    "before": before,
                    "after": value_label,
                }
            )

    rows.extend(_group_rows(project, proposal, skipped))

    order = [str(code) for code in proposal.get("order") or [] if str(code) in questions]
    order = list(dict.fromkeys(order))
    if len(order) >= 2:
        current = [code for code in questions if code in set(order)]
        if current != order:
            rows.append(
                {
                    "kind": "order",
                    "before": current,
                    "after": order,
                }
            )

    for index, row in enumerate(rows, start=1):
        row["id"] = f"r{index}"
    return rows, skipped


def _group_rows(
    project: dict, proposal: dict[str, Any], skipped: list[str]
) -> list[dict[str, Any]]:
    """Группы из одиночных вопросов: каждая проверяется сборкой «всухую».

    Вопрос уходит не больше чем в одну группу; вопрос, на который ссылается
    баннер, фильтр или перекодировка, в группу не уходит — как при ручной
    сборке.
    """
    configuration = project["configuration"]
    questions = configuration["questions"]
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    taken: set[str] = set()
    rows: list[dict[str, Any]] = []
    for item in _records(proposal.get("groups")):
        question_type = str(item.get("question_type") or "")
        codes = list(dict.fromkeys(str(code) for code in item.get("codes") or []))
        name = ", ".join(codes[:4]) + ("…" if len(codes) > 4 else "") or "(без вопросов)"
        if question_type not in QUESTIONNAIRE_GROUP_TYPES:
            skipped.append(f"Группа {name}: тип «{question_type}» по анкете не собирается.")
            continue
        repeated = [code for code in codes if code in taken]
        if repeated:
            skipped.append(
                f"Группа {name}: {', '.join(repeated)} уже в другой предложенной группе."
            )
            continue
        referenced = [code for code in codes if find_references(configuration, "question", code)]
        if referenced:
            skipped.append(
                f"Группа {name}: на {', '.join(referenced)} ссылаются баннер, фильтр или "
                "перекодировка. Сначала снимите связи."
            )
            continue
        label = _clean_label(item.get("label"))
        try:
            group = build_group(questions, variables, codes, question_type, label=label or None)
        except QuestionGroupError as exc:
            skipped.append(f"Группа {name}: {exc}")
            continue
        taken.update(codes)
        members = [question["code"] for question in questions if question["code"] in set(codes)]
        rows.append(
            {
                "kind": "group",
                "question_type": question_type,
                "codes": members,
                "code": group["code"],
                "before": members,
                "after": group["label"],
            }
        )
    return rows


def _records(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]


def _clean_label(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:5000]


def record_override(configuration: dict, row: dict[str, Any]) -> None:
    """Записать подпись переменной или кода в `label_overrides`."""
    overrides = configuration.setdefault("label_overrides", {})
    entry = overrides.setdefault(row["variable"], {})
    if row["kind"] == "variable_label":
        entry["label"] = row["after"]
    else:
        values = entry.setdefault("values", [])
        key = value_key(row["value"])
        values[:] = [item for item in values if value_key(item["value"]) != key]
        values.append({"value": row["value"], "label": row["after"]})


def apply_label_overrides(project: dict) -> None:
    """Наложить сохранённые подписи на описание структуры и пункты вопросов.

    Идемпотентно: вызывается после каждой правки подписей и после
    пересборки описания из SAV.
    """
    overrides = project["configuration"].get("label_overrides") or {}
    if not overrides:
        return
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    for name, entry in overrides.items():
        variable = variables.get(name)
        if variable is None:
            continue
        if entry.get("label"):
            variable["label"] = entry["label"]
        if entry.get("values"):
            labels = list(variable.get("value_labels") or [])
            by_key = {value_key(item["value"]): index for index, item in enumerate(labels)}
            for item in entry["values"]:
                key = value_key(item["value"])
                if key in by_key:
                    labels[by_key[key]] = {**labels[by_key[key]], "label": item["label"]}
                else:
                    by_key[key] = len(labels)
                    labels.append({"value": item["value"], "label": item["label"]})
            variable["value_labels"] = sorted(labels, key=_value_order)
    for question in [
        *project["inspection"].get("questions", []),
        *project["configuration"]["questions"],
    ]:
        for item in question.get("items") or []:
            entry = overrides.get(item.get("variable"))
            if entry and entry.get("label"):
                item["label"] = entry["label"]


def _value_order(item: dict[str, Any]) -> tuple[int, float, str]:
    value = item["value"]
    if isinstance(value, int | float) and not isinstance(value, bool):
        return (0, float(value), "")
    return (1, 0.0, str(value))


def reordered(codes: list[str], order: list[str]) -> list[str]:
    """Порядок вопросов: названные анкетой встают по анкете на свои же места,
    остальные не двигаются."""
    wanted = [code for code in order if code in set(codes)]
    chosen = set(wanted)
    queue = iter(wanted)
    return [next(queue) if code in chosen else code for code in codes]
