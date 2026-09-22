from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .formulas import read_project_frame
from .multiple_response import (
    MultipleResponseError,
    answered_mask,
    response_definition,
    response_options,
    selected_mask,
)


class FilterError(ValueError):
    pass


# Операции, которые имеют смысл для источника условия. Сервер остаётся
# единственным местом, где это записано: редактор получает список вместе
# с вариантами ответа. Прежние `eq` и `ne` по-прежнему принимаются при
# сохранении — это частные случаи `in` и `not_in` с одним значением.
OPERATORS_BY_KIND: dict[str, list[str]] = {
    "categorical": ["in", "not_in", "filled", "missing"],
    "scale": ["in", "not_in", "between", "gt", "lt", "filled", "missing"],
    "numeric": ["between", "gt", "lt", "filled", "missing"],
    "multiple": ["selected_any", "selected_all", "selected_none", "filled", "missing"],
}

# Больше вариантов в списке галочек не читается; такой источник всё равно
# фильтруют диапазоном.
MAX_OPTIONS = 200


def validate_filter(definition: dict[str, Any], project: dict[str, Any]) -> None:
    _validate_group(definition["rule"], project, depth=1)


def calculate_filter_preview(
    path: str | Path, definition: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    validate_filter(definition, project)
    columns = sorted(_required_columns(definition["rule"], project))
    frame = read_project_frame(path, project, columns)
    mask, steps = _evaluate_group(definition["rule"], project, frame, root=True)
    total = len(frame)
    selected = int(mask.sum())
    emptied = next((step for step in steps if step.get("running") == 0), None)
    return {
        "id": definition.get("id"),
        "name": definition["name"],
        "total": total,
        "selected": selected,
        "share": selected / total if total else 0,
        "empty": selected == 0,
        "small_base": 0 < selected < 30,
        "steps": steps,
        "description": describe_rule(definition["rule"], project),
        "emptied_after": emptied["description"] if emptied else None,
    }


def describe_rule(rule: dict[str, Any], project: dict[str, Any]) -> str:
    """Правило фильтра обычным текстом: `Возраст: 18–34 И (Москва ИЛИ Самара)`.

    Единственный форматтер правила. Редактор, строка «База» раздела «Отчёт»
    и `statistics.txt` получают текст отсюда, поэтому проверяемое до сборки
    и записанное в аудит правило не могут разойтись. Коды SPSS заменяются
    подписями значений; код без подписи остаётся числом.
    """
    return _describe_group(rule, project)


def condition_source_options(
    path: str | Path, source: dict[str, str], project: dict[str, Any]
) -> dict[str, Any]:
    """Варианты ответа источника условия с частотами — вместо ввода кодов SPSS."""
    resolved = _resolve_source(source, project)
    kind = _source_kind(source, resolved)
    if source["kind"] == "question":
        columns = list(resolved["source_variables"])
        if kind != "multiple" and len(columns) != 1:
            raise FilterError("Для этого условия нужен одиночный вопрос.")
    else:
        columns = sorted(recoding_columns(resolved, project))
    frame = read_project_frame(path, project, columns)
    total = len(frame)
    variables = _variables(project)
    label = _source_label(source, resolved)
    options: list[dict[str, Any]] = []
    minimum = maximum = None
    if kind == "multiple":
        try:
            answered = answered_mask(frame, resolved)
            categorical = response_definition(resolved).get("encoding") == "categorical"
            for option in response_options(resolved, variables, frame):
                key = option["key"]
                options.append(
                    {
                        "value": key,
                        "label": option["label"]
                        if categorical
                        else _item_label(label, variables.get(key, {}), key),
                        "code": key,
                        "labelled": True,
                        "count": int(selected_mask(frame, resolved, key).sum()),
                    }
                )
        except MultipleResponseError as exc:
            raise FilterError(str(exc)) from exc
        missing = total - int(answered.sum())
    else:
        series = _source_series(source, resolved, frame, project)
        missing = int(series.isna().sum())
        if source["kind"] == "recoding":
            for category in resolved["categories"]:
                name = category["label"]
                count = int(series.map(lambda value, name=name: _matches_any(value, [name])).sum())
                options.append(
                    {"value": name, "label": name, "code": None, "labelled": True, "count": count}
                )
        else:
            numeric = pd.to_numeric(series, errors="coerce")
            if numeric.notna().any():
                minimum, maximum = _scalar(numeric.min()), _scalar(numeric.max())
            if kind != "numeric":
                options = value_options(series, variables.get(columns[0], {}))
    return {
        "source": source,
        "label": label,
        "kind": kind,
        "operators": OPERATORS_BY_KIND[kind],
        "total": total,
        "missing": missing,
        "minimum": minimum,
        "maximum": maximum,
        "options": options[:MAX_OPTIONS],
        "truncated": len(options) > MAX_OPTIONS,
    }


def filter_columns(definition: dict[str, Any], project: dict[str, Any]) -> set[str]:
    """Столбцы SAV, без которых правило не посчитать."""
    return _required_columns(definition["rule"], project)


def recoding_columns(recoding: dict[str, Any], project: dict[str, Any]) -> set[str]:
    """Столбцы SAV перекодировки: исходная переменная или переменные её правил."""
    if recoding.get("mode") == "conditions":
        columns: set[str] = set()
        for category in recoding["categories"]:
            columns |= _required_columns(category["rule"], project)
        return columns
    if recoding.get("mode") == "segments":
        from .segmentation import segment_columns

        return set(segment_columns(recoding, project))
    return {recoding["source_variable"]}


def conditional_series(
    recoding: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> pd.Series:
    """Логическая переменная: каждому — подпись первой категории, чьё правило он проходит.

    Категории не пересекаются по построению: прошедший правило раньше дальше не
    проверяется. Поэтому такую переменную можно ставить в баннер, не решая, как
    сравнивать пересекающиеся колонки. Не прошедший ни одно правило — пропуск.
    """
    result = pd.Series(pd.NA, index=frame.index, dtype="object")
    assigned = pd.Series(False, index=frame.index)
    for category in recoding["categories"]:
        mask, _ = _evaluate_group(category["rule"], project, frame)
        chosen = mask & ~assigned
        result.loc[chosen] = category["label"]
        assigned |= chosen
    return result


def validate_condition_rules(definition: dict[str, Any], project: dict[str, Any]) -> None:
    """Правила логической переменной — обычные правила фильтра, но без ссылок на
    другие логические переменные: так исключены циклы и цепочки, которые
    пришлось бы пересчитывать в правильном порядке."""
    for category in definition["categories"]:
        _validate_group(category["rule"], project, depth=1)
        for source in rule_sources(category["rule"]):
            if source["kind"] != "recoding":
                continue
            target = _resolve_source(source, project)
            if target.get("mode") == "conditions" or (
                definition.get("id") and str(target.get("id")) == str(definition["id"])
            ):
                raise FilterError(
                    "Условие логической переменной не может ссылаться на другую "
                    "логическую переменную."
                )


def rule_sources(group: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for item in group.get("items", []):
        if item.get("kind") == "group":
            sources.extend(rule_sources(item))
        else:
            sources.append(item["source"])
    return sources


def evaluate_filter_frame(
    definition: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> pd.Series:
    """Return a boolean mask for an already loaded SAV frame."""
    validate_filter(definition, project)
    mask, _ = _evaluate_group(definition["rule"], project, frame)
    return mask


def filter_required_columns(definition: dict[str, Any], project: dict[str, Any]) -> list[str]:
    """Исходные столбцы, нужные для расчёта сохранённого правила."""
    validate_filter(definition, project)
    return sorted(_required_columns(definition["rule"], project))


def _validate_group(group: dict[str, Any], project: dict[str, Any], depth: int) -> None:
    if depth > 2:
        raise FilterError("Вложенность фильтра не может превышать два уровня.")
    if not group.get("items"):
        raise FilterError("Добавьте хотя бы одно условие.")
    for item in group["items"]:
        if item["kind"] == "group":
            _validate_group(item, project, depth + 1)
        else:
            _validate_condition(item, project)


def _validate_condition(condition: dict[str, Any], project: dict[str, Any]) -> None:
    source = _resolve_source(condition["source"], project)
    operator = condition["operator"]
    multiple_ops = {"selected", "selected_any", "selected_all", "selected_none"}
    is_multiple = source.get("question_type", "").startswith("multiple_choice")
    if operator in multiple_ops and not is_multiple:
        raise FilterError("Операция выбора вариантов доступна только для multiple-response.")
    if is_multiple and operator not in multiple_ops | {"filled", "missing"}:
        raise FilterError("Для multiple-response выберите операцию по вариантам.")
    if operator in {"eq", "ne", "in", "not_in"} and not condition.get("values"):
        raise FilterError("Укажите значение условия.")
    if operator in multiple_ops and not condition.get("values"):
        raise FilterError("Выберите хотя бы один вариант multiple-response.")
    if operator == "between" and (
        condition.get("lower") is None or condition.get("upper") is None
    ):
        raise FilterError("Для диапазона укажите обе границы.")
    if operator == "gt" and condition.get("lower") is None:
        raise FilterError("Укажите нижнюю границу.")
    if operator == "lt" and condition.get("upper") is None:
        raise FilterError("Укажите верхнюю границу.")


def _resolve_source(source: dict[str, str], project: dict[str, Any]) -> dict[str, Any]:
    key = "questions" if source["kind"] == "question" else "recodings"
    field = "code" if source["kind"] == "question" else "id"
    resolved = next(
        (item for item in project["configuration"][key] if item[field] == source["ref"]),
        None,
    )
    if resolved is None:
        raise FilterError("Источник условия не найден.")
    return resolved


def _required_columns(group: dict[str, Any], project: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in group["items"]:
        if item["kind"] == "group":
            result |= _required_columns(item, project)
            continue
        resolved = _resolve_source(item["source"], project)
        if item["source"]["kind"] == "question":
            result.update(resolved["source_variables"])
        else:
            result |= recoding_columns(resolved, project)
    return result


def _evaluate_group(
    group: dict[str, Any],
    project: dict[str, Any],
    frame: pd.DataFrame,
    *,
    root: bool = False,
) -> tuple[pd.Series, list[dict[str, Any]]]:
    """Маска группы и шаги расчёта.

    `selected` — сколько подходит под само условие, `running` — сколько
    осталось после него вместе с предыдущими условиями верхнего уровня.
    Второе и отвечает на вопрос, на каком условии обнулилась выборка.
    """
    result: pd.Series | None = None
    steps: list[dict[str, Any]] = []
    for item in group["items"]:
        if item["kind"] == "group":
            mask, nested_steps = _evaluate_group(item, project, frame)
            steps.extend(nested_steps)
        else:
            mask = _evaluate_condition(item, project, frame)
        mask = mask.fillna(False).astype(bool)
        if result is None:
            result = mask.copy()
        else:
            result = result & mask if group["operator"] == "and" else result | mask
        step: dict[str, Any] = {
            "description": _describe_item(item, project),
            "selected": int(mask.sum()),
        }
        if root:
            step["running"] = int(result.sum())
        steps.append(step)
    if result is None:
        raise FilterError("Добавьте хотя бы одно условие.")
    return result, steps


def _evaluate_condition(
    condition: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> pd.Series:
    resolved = _resolve_source(condition["source"], project)
    operator = condition["operator"]
    if condition["source"]["kind"] == "question" and resolved.get(
        "question_type", ""
    ).startswith("multiple_choice"):
        try:
            selected = [
                selected_mask(frame, resolved, name)
                for name in condition.get("values", [])
            ]
            available = answered_mask(frame, resolved)
        except MultipleResponseError as exc:
            raise FilterError(str(exc)) from exc
        if operator in {"selected", "selected_any"}:
            return pd.concat(selected, axis=1).any(axis=1)
        if operator == "selected_all":
            return pd.concat(selected, axis=1).all(axis=1)
        if operator == "selected_none":
            return available & ~pd.concat(selected, axis=1).any(axis=1)
        return available if operator == "filled" else ~available

    series = _source_series(condition["source"], resolved, frame, project)
    values = condition.get("values", [])
    if operator == "filled":
        return series.notna()
    if operator == "missing":
        return series.isna()
    if operator in {"eq", "in"}:
        return series.map(lambda value: _matches_any(value, values))
    if operator in {"ne", "not_in"}:
        return series.notna() & ~series.map(lambda value: _matches_any(value, values))
    numeric = pd.to_numeric(series, errors="coerce")
    if operator == "gt":
        return numeric > condition["lower"]
    if operator == "lt":
        return numeric < condition["upper"]
    if operator == "between":
        return numeric.between(condition["lower"], condition["upper"], inclusive="both")
    raise FilterError("Неизвестная операция фильтра.")


def _source_series(
    source: dict[str, str],
    resolved: dict[str, Any],
    frame: pd.DataFrame,
    project: dict[str, Any] | None = None,
) -> pd.Series:
    if source["kind"] == "question":
        if len(resolved["source_variables"]) != 1:
            raise FilterError("Для этого условия нужен одиночный вопрос.")
        return frame[resolved["source_variables"][0]]
    if resolved.get("mode") == "conditions":
        if project is None:
            raise FilterError("Для логической переменной нужен проект.")
        return conditional_series(resolved, project, frame)
    if resolved.get("mode") == "segments":
        if project is None:
            raise FilterError("Для сегментации нужен проект.")
        from .segmentation import segment_series

        return segment_series(resolved, project, frame)
    series = frame[resolved["source_variable"]]
    result = pd.Series(pd.NA, index=series.index, dtype="object")
    for category in resolved["categories"]:
        if resolved.get("mode", "ranges") == "categories":
            category_values = category["values"]
            mask = series.map(
                lambda value, expected=category_values: _matches_any(value, expected)
            )
        else:
            numeric = pd.to_numeric(series, errors="coerce")
            mask = numeric.notna()
            if category.get("lower") is not None:
                mask &= numeric >= category["lower"]
            if category.get("upper") is not None:
                mask &= numeric <= category["upper"]
        result.loc[mask] = category["label"]
    return result


def _matches_any(value: Any, expected: list[Any]) -> bool:
    if pd.isna(value):
        return False
    return any(value == item or str(value) == str(item) for item in expected)


def _describe_group(
    group: dict[str, Any], project: dict[str, Any], *, nested: bool = False
) -> str:
    separator = " И " if group["operator"] == "and" else " ИЛИ "
    parts = [_describe_item(item, project) for item in group["items"]]
    text = separator.join(parts)
    return f"({text})" if nested and len(parts) > 1 else text


def _describe_item(item: dict[str, Any], project: dict[str, Any]) -> str:
    if item["kind"] == "group":
        return _describe_group(item, project, nested=True)
    source = item["source"]
    resolved = _resolve_source(source, project)
    label = _source_label(source, resolved)
    values = [_value_text(source, resolved, project, value) for value in item.get("values", [])]
    operator = item["operator"]
    if operator in {"eq", "in"}:
        return f"{label}: {' или '.join(values)}"
    if operator in {"ne", "not_in"}:
        return f"{label}: кроме {', '.join(values)}"
    if operator == "gt":
        return f"{label} > {_code(item['lower'])}"
    if operator == "lt":
        return f"{label} < {_code(item['upper'])}"
    if operator == "between":
        return f"{label}: {_code(item['lower'])}–{_code(item['upper'])}"
    if operator == "filled":
        return f"{label}: ответ есть"
    if operator == "missing":
        return f"{label}: пропуск"
    if operator in {"selected", "selected_any"}:
        if len(values) == 1:
            return f"{label}: выбран {values[0]}"
        return f"{label}: выбран хотя бы один из {', '.join(values)}"
    if operator == "selected_all":
        return f"{label}: выбраны все из {', '.join(values)}"
    if operator == "selected_none":
        return f"{label}: не выбран ни один из {', '.join(values)}"
    raise FilterError("Неизвестная операция фильтра.")


def _source_kind(source: dict[str, str], resolved: dict[str, Any]) -> str:
    if source["kind"] == "recoding":
        return "categorical"
    question_type = str(resolved.get("question_type", ""))
    if question_type.startswith("multiple_choice"):
        return "multiple"
    if question_type == "single_choice":
        return "categorical"
    if question_type in {"scale", "numeric"}:
        return question_type
    raise FilterError("Этот вопрос нельзя использовать в условии фильтра.")


def _source_label(source: dict[str, str], resolved: dict[str, Any]) -> str:
    if source["kind"] == "question":
        label = str(resolved.get("label") or source["ref"])
    else:
        label = str(resolved.get("name") or resolved.get("code") or source["ref"])
    # «Ваш пол?: Женщина» читается хуже, чем «Ваш пол: Женщина».
    return label.strip().rstrip("?:.").strip() or source["ref"]


def _value_text(
    source: dict[str, str], resolved: dict[str, Any], project: dict[str, Any], value: Any
) -> str:
    if source["kind"] == "recoding":
        return str(value)
    variables = _variables(project)
    if resolved.get("question_type") == "multiple_choice_dichotomy":
        name = str(value)
        return _item_label(_source_label(source, resolved), variables.get(name, {}), name)
    # У категориального multiple вариант — код, и подпись ищется в слотах
    # так же, как у одиночного вопроса.
    for name in resolved.get("source_variables", []):
        for item in variables.get(name, {}).get("value_labels", []):
            if _key(item["value"]) == _key(value):
                return str(item["label"])
    return _code(value)


def _item_label(question_label: str, variable: dict[str, Any], name: str) -> str:
    """Подпись варианта multiple без повторения самого вопроса."""
    label = str(variable.get("label") or name)
    if question_label and label.startswith(question_label):
        rest = label[len(question_label):].lstrip(" :—–-").strip()
        if rest:
            return rest
    return label


def value_options(series: pd.Series, variable: dict[str, Any]) -> list[dict[str, Any]]:
    """Значения переменной с подписями и частотами: подписанные по порядку подписей,
    затем неподписанные по возрастанию.

    Общая для условий фильтра и групп перекодировки, чтобы один и тот же ответ
    не показывался в двух местах с разной частотой.
    """
    counts: dict[Any, tuple[Any, int]] = {}
    for value, count in series.dropna().value_counts().items():
        counts[_key(value)] = (_scalar(value), int(count))
    options = []
    for item in variable.get("value_labels", []):
        observed = counts.pop(_key(item["value"]), (item["value"], 0))
        options.append(
            {
                "value": _scalar(item["value"]),
                "label": str(item["label"]),
                "code": _code(item["value"]),
                "labelled": True,
                "count": observed[1],
            }
        )
    # Код без подписи тоже ответ: он показывается числом после подписанных.
    for value, count in sorted(counts.values(), key=lambda pair: _sort_key(pair[0])):
        options.append(
            {
                "value": value,
                "label": _code(value),
                "code": _code(value),
                "labelled": False,
                "count": count,
            }
        )
    return options


def _variables(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["name"]: item
        for item in (project.get("inspection") or {}).get("variables", [])
    }


def _key(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _sort_key(value: Any) -> tuple[int, float, str]:
    key = _key(value)
    return (0, key, "") if isinstance(key, float) else (1, 0.0, str(key))


def _scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _code(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)
