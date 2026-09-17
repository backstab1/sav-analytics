"""Формулы: числовые переменные, вычисленные из переменных массива.

Выражение пишется привычно для Excel и SPSS: `MEAN(Q1, Q2, Q3) * 20`,
`IF(AGE >= 18 AND SEX = 1, 1, 0)`, `COUNT(1, Q5_1, Q5_2)`. Имя переменной с
точкой или другим знаком берётся в квадратные скобки: `[Q1.2] + 1`.

Разбор идёт через `ast` Python с белым списком узлов — никакого `eval`.
Правила пропусков те же, что в SPSS:

- арифметика и сравнение с пропуском дают пропуск, деление на ноль — тоже;
- `SUM`, `MEAN`, `MIN`, `MAX` берут заполненные аргументы и пусты, только
  если пусты все;
- `AND` и `OR` трёхзначные: `ложь AND пропуск` — ложь, `истина OR пропуск` —
  истина, иначе пропуск;
- `COUNT(значение, поля…)` считает поля, равные значению, пропуск не равен
  ничему, поэтому результат всегда есть.

Результат формулы хранится как производная переменная проекта: чтения SAV
идут через :func:`read_project_frame`, который досчитывает нужные формулы, так
что таблицы, фильтры, баннеры и перекодировки видят её как обычную колонку.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyreadstat

from .open_text import codeframe_columns, codeframe_text_variable, theme_owners


class FormulaError(ValueError):
    pass


MAX_EXPRESSION = 2000
_BRACKETED = re.compile(r"\[([^\[\]]+)\]")
_WORDS = {"AND": "and", "OR": "or", "NOT": "not"}
# Сколько аргументов принимает функция: (минимум, максимум или None).
FUNCTIONS: dict[str, tuple[int, int | None]] = {
    "SUM": (1, None),
    "MEAN": (1, None),
    "MIN": (1, None),
    "MAX": (1, None),
    "COUNT": (2, None),
    "IF": (3, 3),
    "ABS": (1, 1),
    "ROUND": (1, 2),
}
_BINARY = {ast.Add, ast.Sub, ast.Mult, ast.Div}
_COMPARE = {ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE}


def parse_formula(expression: str) -> tuple[ast.Expression, dict[str, str]]:
    """Разобрать выражение; вернуть дерево и имена переменных по заменителям."""
    if not expression.strip():
        raise FormulaError("Формула пуста.")
    if len(expression) > MAX_EXPRESSION:
        raise FormulaError(f"Формула длиннее {MAX_EXPRESSION} знаков.")
    names: dict[str, str] = {}

    def bracket(match: re.Match[str]) -> str:
        placeholder = f"__v{len(names)}"
        names[placeholder] = match.group(1).strip()
        return placeholder

    text = _BRACKETED.sub(bracket, expression)
    text = text.replace("<>", "!=")
    text = re.sub(r"(?<![<>!=])=(?!=)", "==", text)
    text = re.sub(
        r"\b(AND|OR|NOT)\b", lambda match: _WORDS[match.group(1).upper()], text,
        flags=re.IGNORECASE,
    )
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise FormulaError("Формула записана с ошибкой: проверьте скобки и знаки.") from exc
    _check(tree.body, names)
    return tree, names


def formula_variables(expression: str) -> list[str]:
    """Переменные массива, на которых стоит формула, в порядке появления."""
    tree, names = parse_formula(expression)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not _is_function_name(tree, node):
            name = names.get(node.id, node.id)
            if name not in found:
                found.append(name)
    return found


def _is_function_name(tree: ast.Expression, target: ast.Name) -> bool:
    return any(
        isinstance(node, ast.Call) and node.func is target for node in ast.walk(tree)
    )


def _check(node: ast.AST, names: dict[str, str]) -> None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise FormulaError("В формуле допустимы только числа, переменные и функции.")
        return
    if isinstance(node, ast.Name):
        return
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BINARY:
            raise FormulaError("Из арифметики доступны только + − * /.")
        _check(node.left, names)
        _check(node.right, names)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.USub | ast.UAdd | ast.Not):
            raise FormulaError("Неизвестная операция в формуле.")
        _check(node.operand, names)
        return
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            _check(value, names)
        return
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or type(node.ops[0]) not in _COMPARE:
            raise FormulaError("Сравнение записывается одним знаком: = <> < <= > >=.")
        _check(node.left, names)
        _check(node.comparators[0], names)
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise FormulaError("Неизвестная функция в формуле.")
        function = node.func.id.upper()
        if function not in FUNCTIONS:
            known = ", ".join(FUNCTIONS)
            raise FormulaError(f"Функции {node.func.id} нет. Доступны: {known}.")
        least, most = FUNCTIONS[function]
        if len(node.args) < least or (most is not None and len(node.args) > most):
            raise FormulaError(f"У {function} другое число аргументов.")
        if any(isinstance(argument, ast.Starred) for argument in node.args):
            raise FormulaError("Неизвестная запись аргументов в формуле.")
        for argument in node.args:
            _check(argument, names)
        return
    raise FormulaError("В формуле допустимы только числа, переменные, знаки и функции.")


def evaluate_formula(expression: str, frame: pd.DataFrame) -> pd.Series:
    """Посчитать формулу по строкам массива; пропуск — NaN."""
    tree, names = parse_formula(expression)
    result = _evaluate(tree.body, names, frame)
    series = result if isinstance(result, pd.Series) else pd.Series(result, index=frame.index)
    return series.astype(float).replace([np.inf, -np.inf], np.nan)


Value = pd.Series | float


def _evaluate(node: ast.AST, names: dict[str, str], frame: pd.DataFrame) -> Value:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        name = names.get(node.id, node.id)
        if name not in frame.columns:
            raise FormulaError(f"Переменной {name} нет в массиве.")
        return pd.to_numeric(frame[name], errors="coerce").astype(float)
    if isinstance(node, ast.BinOp):
        left = _series(_evaluate(node.left, names, frame), frame)
        right = _series(_evaluate(node.right, names, frame), frame)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right.where(right != 0)
    if isinstance(node, ast.UnaryOp):
        operand = _series(_evaluate(node.operand, names, frame), frame)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        return (operand == 0).astype(float).where(operand.notna())
    if isinstance(node, ast.Compare):
        left = _series(_evaluate(node.left, names, frame), frame)
        right = _series(_evaluate(node.comparators[0], names, frame), frame)
        operation = type(node.ops[0])
        compared = {
            ast.Eq: left == right,
            ast.NotEq: left != right,
            ast.Lt: left < right,
            ast.LtE: left <= right,
            ast.Gt: left > right,
            ast.GtE: left >= right,
        }[operation]
        return compared.astype(float).where(left.notna() & right.notna())
    if isinstance(node, ast.BoolOp):
        values = [
            _series(_evaluate(value, names, frame), frame) for value in node.values
        ]
        known = pd.concat([value.notna() for value in values], axis=1)
        truth = pd.concat([(value != 0) & value.notna() for value in values], axis=1)
        falsity = pd.concat([(value == 0) for value in values], axis=1)
        if isinstance(node.op, ast.And):
            result = pd.Series(np.nan, index=frame.index)
            result[known.all(axis=1)] = 1.0
            result[falsity.any(axis=1)] = 0.0
        else:
            result = pd.Series(np.nan, index=frame.index)
            result[known.all(axis=1)] = 0.0
            result[truth.any(axis=1)] = 1.0
        return result
    if isinstance(node, ast.Call):
        return _call(node, names, frame)
    raise FormulaError("В формуле допустимы только числа, переменные, знаки и функции.")


def _call(node: ast.Call, names: dict[str, str], frame: pd.DataFrame) -> pd.Series:
    function = node.func.id.upper()  # type: ignore[attr-defined]
    arguments = [_series(_evaluate(argument, names, frame), frame) for argument in node.args]
    if function == "IF":
        condition, when_true, when_false = arguments
        result = when_false.where(condition == 0, when_true)
        return result.where(condition.notna())
    if function == "ABS":
        return arguments[0].abs()
    if function == "ROUND":
        digits = 0
        if len(arguments) == 2:
            unique = arguments[1].dropna().unique()
            if len(unique) != 1 or float(unique[0]) != int(unique[0]):
                raise FormulaError("Число знаков в ROUND должно быть целым числом.")
            digits = int(unique[0])
        return arguments[0].round(digits)
    if function == "COUNT":
        target, *fields = arguments
        table = pd.concat(fields, axis=1)
        return table.eq(target, axis=0).sum(axis=1).astype(float)
    table = pd.concat(arguments, axis=1)
    if function == "SUM":
        return table.sum(axis=1, min_count=1)
    if function == "MEAN":
        return table.mean(axis=1)
    if function == "MIN":
        return table.min(axis=1)
    return table.max(axis=1)


def _series(value: Value, frame: pd.DataFrame) -> pd.Series:
    if isinstance(value, pd.Series):
        return value
    return pd.Series(value, index=frame.index, dtype=float)


NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def validate_formula(
    definition: dict[str, Any], project: dict[str, Any], *, formula_id: str | None = None
) -> list[str]:
    """Проверить формулу проекта; вернуть переменные, на которых она стоит."""
    name = definition["name"]
    if not NAME_PATTERN.match(name):
        raise FormulaError("Имя формулы — латиница, цифры и _, начиная с буквы.")
    formulas = project["configuration"].get("formulas", [])
    own = {
        item["name"] for item in formulas if formula_id is not None and item["id"] == formula_id
    }
    taken = {
        item["name"] for item in project["inspection"]["variables"] if item["name"] not in own
    } | {
        item["code"]
        for item in project["configuration"]["questions"]
        if item.get("formula_id") != formula_id or formula_id is None
    }
    if name in taken:
        raise FormulaError(f"Имя {name} уже занято переменной или вопросом.")
    variables = {
        item["name"]: item
        for item in project["inspection"]["variables"]
        if not item.get("formula_id")
    }
    others = {
        item["name"]: item
        for item in formulas
        if formula_id is None or item["id"] != formula_id
    }
    sources = formula_variables(definition["expression"])
    if not sources:
        raise FormulaError("Формула должна опираться хотя бы на одну переменную массива.")
    for source in sources:
        if source == name:
            raise FormulaError("Формула не может ссылаться на саму себя.")
        if source in others:
            continue
        if source not in variables:
            raise FormulaError(f"Переменной {source} нет в массиве.")
        if variables[source].get("storage_type") != "numeric":
            raise FormulaError(f"{source} — текстовая переменная, в формуле нужны числовые.")
    # Цикл: формула, на которую мы ссылаемся, сама (через цепочку) ссылается на нас.
    candidate = {**others, name: {**definition, "name": name}}
    _check_cycles(candidate)
    return sources


def _check_cycles(formulas: dict[str, dict[str, Any]]) -> None:
    state: dict[str, str] = {}

    def visit(name: str, chain: list[str]) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "active":
            loop = " → ".join([*chain[chain.index(name):], name])
            raise FormulaError(f"Формулы ссылаются друг на друга по кругу: {loop}.")
        state[name] = "active"
        for source in formula_variables(formulas[name]["expression"]):
            if source in formulas:
                visit(source, [*chain, name])
        state[name] = "done"

    for name in formulas:
        visit(name, [])


def formula_order(formulas: dict[str, dict[str, Any]], wanted: Iterable[str]) -> list[str]:
    """Формулы в порядке вычисления: зависимости раньше зависящих."""
    order: list[str] = []

    def visit(name: str) -> None:
        if name in order:
            return
        for source in formula_variables(formulas[name]["expression"]):
            if source in formulas:
                visit(source)
        order.append(name)

    for name in wanted:
        visit(name)
    return order


def base_columns(formulas: dict[str, dict[str, Any]], names: Iterable[str]) -> list[str]:
    """Столбцы SAV, из которых считаются формулы, со всей цепочкой."""
    columns: list[str] = []
    for name in formula_order(formulas, names):
        for source in formula_variables(formulas[name]["expression"]):
            if source not in formulas and source not in columns:
                columns.append(source)
    return columns


def formula_preview(
    path: str | Path, definition: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    """Итог формулы до сохранения: сколько посчиталось и из-за чего пусто.

    Для каждого поля — у скольких респондентов результат пуст, а это поле
    не заполнено: так видно, какое поле «съедает» базу.
    """
    sources = validate_formula(definition, project, formula_id=definition.get("id"))
    frame = read_project_frame(path, _without_formula(project, definition.get("id")), sources)
    series = evaluate_formula(definition["expression"], frame)
    empty = series.isna()
    labels = {item["name"]: item.get("label") for item in project["inspection"]["variables"]}
    valid = series.dropna()
    return {
        "total": len(series),
        "valid": int(valid.size),
        "missing": int(empty.sum()),
        "mean": float(valid.mean()) if valid.size else None,
        "min": float(valid.min()) if valid.size else None,
        "max": float(valid.max()) if valid.size else None,
        "missing_by_variable": [
            {
                "variable": source,
                "label": labels.get(source) or source,
                "count": int((empty & frame[source].isna()).sum()),
            }
            for source in sources
        ],
    }


def _without_formula(project: dict[str, Any], formula_id: str | None) -> dict[str, Any]:
    """Проект без правимой формулы: её старая версия не должна считаться источником."""
    if formula_id is None or "configuration" not in project:
        return project
    configuration = project["configuration"]
    return {
        **project,
        "configuration": {
            **configuration,
            "formulas": [
                item for item in configuration.get("formulas", []) if item["id"] != formula_id
            ],
        },
    }


def formula_statistics(
    path: str | Path, definition: dict[str, Any], project: dict[str, Any] | None = None
) -> dict[str, int]:
    """Счётчики производной переменной для структуры проекта."""
    sources = formula_variables(definition["expression"])
    frame = read_project_frame(path, _without_formula(project or {}, definition.get("id")), sources)
    series = evaluate_formula(definition["expression"], frame)
    return {
        "valid_count": int(series.notna().sum()),
        "missing_count": int(series.isna().sum()),
        "unique_count": int(series.dropna().nunique()),
    }


def read_project_frame(
    path: str | Path, project: dict[str, Any] | None, columns: Iterable[str] | None = None
) -> pd.DataFrame:
    """Прочитать SAV и досчитать производные столбцы, которые просят колонки.

    Производные — формулы и темы открытых ответов. Без `columns` читается весь
    файл и считаются все производные, как для книги. Темы считаются раньше
    формул: формула может опираться на тему.
    """
    formulas = {
        item["name"]: item
        for item in ((project or {}).get("configuration") or {}).get("formulas", [])
    }
    owners = theme_owners(project)
    if columns is None:
        wanted = formula_order(formulas, formulas)
        themes = list(owners)
        usecols = None
    else:
        requested = list(dict.fromkeys(columns))
        wanted = formula_order(formulas, [name for name in requested if name in formulas])
        needed = [name for name in requested if name not in formulas] + base_columns(
            formulas, wanted
        )
        themes = [name for name in dict.fromkeys(needed) if name in owners]
        texts = [
            codeframe_text_variable(owners[name], project) for name in themes  # type: ignore[arg-type]
        ]
        usecols = list(
            dict.fromkeys([name for name in needed if name not in owners] + texts)
        )
    frame = _read_sav(path, usecols)
    computed: set[str] = set()
    for name in themes:
        codeframe = owners[name]
        if codeframe["id"] in computed:
            continue
        computed.add(codeframe["id"])
        text_variable = codeframe_text_variable(codeframe, project)  # type: ignore[arg-type]
        for column, values in codeframe_columns(frame[text_variable], codeframe).items():
            frame[column] = values
    for name in wanted:
        frame[name] = evaluate_formula(formulas[name]["expression"], frame)
    return frame


def _read_sav(path: str | Path, columns: list[str] | None) -> pd.DataFrame:
    frame, _ = pyreadstat.read_sav(
        path,
        usecols=columns,
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    return frame


def formula_variable(definition: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    """Запись производной переменной в `inspection.variables`."""
    return {
        "name": definition["name"],
        "label": definition["label"],
        "storage_type": "numeric",
        "original_format": None,
        "measurement_level": "scale",
        "question_type": "numeric",
        "role": "question",
        "valid_count": counts["valid_count"],
        "missing_count": counts["missing_count"],
        "unique_count": counts["unique_count"],
        "value_labels": [],
        "warnings": [],
        "formula_id": definition["id"],
    }


def formula_question(definition: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    """Вопрос структуры для формулы: числовой, сразу в отчёте."""
    return {
        "code": definition["name"],
        "label": definition["label"],
        "question_type": "numeric",
        "role": "question",
        "source_variables": [definition["name"]],
        "valid_count": counts["valid_count"],
        "missing_count": counts["missing_count"],
        "included_in_report": True,
        "recognition": "manual",
        "warnings": [],
        "items": [],
        "special_values": [],
        "special_items": [],
        "multiple_response": None,
        "formula_id": definition["id"],
    }
