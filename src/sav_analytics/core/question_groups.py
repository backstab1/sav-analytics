"""Ручная сборка и разборка групп вопросов (PQ.9, P1.4).

Автоопределение собирает группы из метаданных SPSS и по именам вида `Q5_1`,
но слоты без общего префикса или с чужими подписями остаются отдельными
вопросами. Аналитик собирает их сам: несколько одиночных вопросов становятся
одним multiple или матрицей и так же разбираются обратно.

Модуль только строит новые вопросы. Где их хранить и что им мешает — ссылки
баннеров и фильтров, запись проекта — решает репозиторий.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .inference import group_prefix, is_special_label

GROUP_TYPES = frozenset(
    {"multiple_choice_dichotomy", "multiple_choice_categorical", "matrix"}
)
#: Признак ручной группы. Перераспознавание структуры строит вопросы заново
#: из SAV и ручную группу не нашло бы — по этому признаку оно её переносит.
MANUAL = "manual"


class QuestionGroupError(ValueError):
    """Группу собрать или разобрать нельзя."""


def build_group(
    questions: list[dict[str, Any]],
    variables: dict[str, dict[str, Any]],
    codes: list[str],
    question_type: str,
    *,
    code: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Вопрос-группа из выбранных одиночных вопросов.

    Слоты идут в порядке вопросов в структуре, а не в порядке выбора:
    так варианты в книге стоят там же, где аналитик их видел.
    """
    if question_type not in GROUP_TYPES:
        raise QuestionGroupError("Собрать можно multiple-response или матрицу.")
    wanted = list(dict.fromkeys(codes))
    if len(wanted) < 2:
        raise QuestionGroupError("Для группы выберите хотя бы два вопроса.")
    by_code = {item["code"]: item for item in questions}
    missing = [item for item in wanted if item not in by_code]
    if missing:
        raise QuestionGroupError("Вопросы не найдены: " + ", ".join(missing) + ".")
    members = [item for item in questions if item["code"] in wanted]
    for member in members:
        if len(member["source_variables"]) != 1:
            raise QuestionGroupError(
                f"{member['code']} уже группа. Сначала разгруппируйте её."
            )
        if member.get("formula_id") or member.get("codeframe_id"):
            raise QuestionGroupError(
                f"{member['code']} — производная переменная, её в группу не собрать."
            )
        if member["role"] != "question":
            raise QuestionGroupError(f"{member['code']} — не вопрос, а служебная переменная.")
    sources = [member["source_variables"][0] for member in members]
    _check_compatible(question_type, [variables[name] for name in sources])

    taken = {item["code"] for item in questions if item["code"] not in wanted}
    final_code = (code or "").strip() or _suggest_code(sources, taken)
    if final_code in taken:
        raise QuestionGroupError(f"Код {final_code} уже занят другим вопросом.")
    item_labels = [variables[name].get("label") or name for name in sources]
    final_label = (label or "").strip() or _common_label(item_labels) or final_code
    group: dict[str, Any] = {
        "code": final_code,
        "label": final_label,
        "question_type": question_type,
        "role": "question",
        "source_variables": sources,
        "valid_count": 0,
        "missing_count": 0,
        "included_in_report": any(member["included_in_report"] for member in members),
        "recognition": MANUAL,
        "warnings": [],
        "items": [
            {"variable": name, "label": text}
            for name, text in zip(sources, item_labels, strict=True)
        ],
        "special_values": _special_values(variables[name] for name in sources)
        if question_type != "multiple_choice_dichotomy"
        else [],
        "special_items": [
            name for name, text in zip(sources, item_labels, strict=True) if is_special_label(text)
        ]
        if question_type == "multiple_choice_dichotomy"
        else [],
        "multiple_response": _response_definition(question_type),
        "base_filter_id": None,
        "group_source": MANUAL,
    }
    return group


def split_group(
    group: dict[str, Any], variables: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Одиночные вопросы из группы: по вопросу на исходную переменную.

    Тип каждого — тот, что распознал для самой переменной SAV-ридер; в отчёт
    переменная входит, если входила группа.
    """
    if len(group["source_variables"]) < 2:
        raise QuestionGroupError("Это не группа: у вопроса одна переменная.")
    if group.get("formula_id") or group.get("codeframe_id"):
        raise QuestionGroupError("Производный вопрос не разгруппировывается.")
    singles = []
    for name in group["source_variables"]:
        variable = variables[name]
        question_type = variable["question_type"]
        singles.append(
            {
                "code": name,
                "label": variable.get("label") or name,
                "question_type": question_type,
                "role": variable.get("role") or "question",
                "source_variables": [name],
                "valid_count": int(variable.get("valid_count") or 0),
                "missing_count": int(variable.get("missing_count") or 0),
                "included_in_report": group["included_in_report"]
                and question_type not in {"open_text", "technical"},
                "recognition": MANUAL,
                "warnings": [],
                "items": [],
                "special_values": _special_values([variable]),
                "special_items": [],
                "multiple_response": None,
                "base_filter_id": None,
            }
        )
    return singles


def carry_manual_groups(
    previous: list[dict[str, Any]],
    detected: list[dict[str, Any]],
    variables: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Перенести ручные группы через перераспознавание структуры.

    Вопросы, которые SAV-ридер нашёл заново на тех же переменных, уступают
    место ручной группе: иначе переменная попала бы в отчёт дважды. Группа,
    у которой из нового файла пропали переменные, держится на оставшихся,
    а меньше двух — распадается, и её переменные возвращаются тем, что нашёл
    ридер.
    """
    result = list(detected)
    for group in (item for item in previous if item.get("group_source") == MANUAL):
        sources = [name for name in group["source_variables"] if name in variables]
        if len(sources) < 2:
            continue
        taken = set(sources)
        position = next(
            (
                index
                for index, item in enumerate(result)
                if taken & set(item["source_variables"])
            ),
            len(result),
        )
        result = [item for item in result if not taken & set(item["source_variables"])]
        result = [item for item in result if item["code"] != group["code"]]
        result.insert(min(position, len(result)), dict(group, source_variables=sources))
    return result


def _check_compatible(question_type: str, variables: list[dict[str, Any]]) -> None:
    if any(variable.get("storage_type") != "numeric" for variable in variables):
        raise QuestionGroupError("В группу собираются только числовые переменные.")
    if question_type == "multiple_choice_dichotomy":
        wide = [
            variable["name"]
            for variable in variables
            if len(variable.get("value_labels") or []) > 2
        ]
        if wide:
            raise QuestionGroupError(
                "У дихотомии в каждой переменной не больше двух кодов, а у "
                + ", ".join(wide)
                + " их больше. Возможно, это категориальный multiple."
            )
        return
    if question_type == "multiple_choice_categorical":
        seen: dict[str, str] = {}
        for variable in variables:
            for item in variable.get("value_labels") or []:
                key = _code_key(item["value"])
                text = str(item["label"]).strip()
                if seen.setdefault(key, text).casefold() != text.casefold():
                    raise QuestionGroupError(
                        f"Код {key} подписан в слотах по-разному: «{seen[key]}» и «{text}». "
                        "В категориальном multiple код значит одно и то же во всех слотах."
                    )
        return
    signatures = {
        tuple((_code_key(item["value"]), str(item["label"]).casefold()) for item in labels)
        for labels in (variable.get("value_labels") or [] for variable in variables)
    }
    if len(signatures) > 1:
        raise QuestionGroupError(
            "Матрица собирается из переменных с одинаковой шкалой: подписи кодов "
            "у выбранных переменных различаются."
        )
    if signatures == {()} and not all(
        variable["question_type"] in {"scale", "numeric"} for variable in variables
    ):
        raise QuestionGroupError("У выбранных переменных нет общей шкалы для матрицы.")


def _response_definition(question_type: str) -> dict[str, Any] | None:
    if question_type == "multiple_choice_dichotomy":
        return {"encoding": "dichotomy", "counted_value": 1, "source": MANUAL}
    if question_type == "multiple_choice_categorical":
        return {"encoding": "categorical", "source": MANUAL}
    return None


def _special_values(variables: Iterable[dict[str, Any]]) -> list[Any]:
    values: list[Any] = []
    for variable in variables:
        for item in variable.get("value_labels") or []:
            if is_special_label(str(item["label"])) and item["value"] not in values:
                values.append(item["value"])
    return values


def _suggest_code(sources: list[str], taken: set[str]) -> str:
    prefixes = {group_prefix(name) for name in sources}
    base = prefixes.pop() if len(prefixes) == 1 and None not in prefixes else None
    base = base or f"{sources[0]}_grp"
    candidate = base
    number = 2
    while candidate in taken:
        candidate = f"{base}_{number}"
        number += 1
    return candidate[:64]


def _common_label(labels: list[str]) -> str | None:
    words = [label.split() for label in labels]
    common: list[str] = []
    for group in zip(*words, strict=False):
        if len(set(group)) != 1:
            break
        common.append(group[0])
    return " ".join(common).rstrip(" :-–—") or None


def _code_key(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)
