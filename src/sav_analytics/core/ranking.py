"""Строгое полное ранжирование в двух представлениях, без изменения SAV."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .not_applicable import applicable_series


class RankingError(ValueError):
    pass


def ranking_items(
    frame: pd.DataFrame, question: dict[str, Any], variables: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Вернуть объекты с сериями мест; ошибочный непустой ответ блокирует расчёт.

    Отсутствующие значения уже очищены от SPSS user-missing при чтении массива.
    Пустой ответ не участвует в среднем; нулевое/дробное/повторное место,
    неизвестный объект и частичный ответ не превращаются молча в пропуск.
    """
    sources = question["source_variables"]
    encoding = question.get("ranking_encoding")
    if len(sources) < 2 or len(set(sources)) != len(sources):
        raise RankingError("Для ранжирования нужны две и более разные переменные.")
    if encoding not in {"rank_per_item", "item_per_rank"}:
        raise RankingError("Укажите структуру ранжирования: место у объекта или объект на месте.")
    if any(name not in frame or name not in variables for name in sources):
        raise RankingError("Не найдены исходные переменные ранжирования.")
    if any(variables[name].get("storage_type") != "numeric" for name in sources):
        raise RankingError("Ранжирование собирается только из числовых переменных.")
    values = pd.DataFrame({name: applicable_series(frame[name], question) for name in sources})
    numeric = values.apply(pd.to_numeric, errors="coerce")
    size = len(sources)
    labels: dict[float, str] = {}
    if encoding == "item_per_rank":
        for name in sources:
            for item in variables[name].get("value_labels", []):
                code = float(item["value"])
                # SPSS declared missing and explicit branch codes are not objects.
                if _missing_code(code, variables[name], question):
                    continue
                label = str(item["label"])
                if code in labels and labels[code] != label:
                    raise RankingError(f"Код {code:g} подписан на разных местах по-разному.")
                labels[code] = label
        if len(labels) != size:
            raise RankingError(
                f"Для {size} мест нужны подписи ровно {size} объектов во входных переменных."
            )
        expected = set(labels)
    else:
        expected = set(range(1, size + 1))
    empty = values.isna().all(axis=1)
    valid = numeric.isin(expected).all(axis=1) & numeric.nunique(axis=1).eq(size)
    invalid = ~empty & ~valid
    if invalid.any():
        raise RankingError(
            f"Ранжирование {question['code']}: некорректных ответов — {int(invalid.sum())}. "
            f"Нужны все {size} объектов на разных местах 1–{size}, без повторов и пропусков; "
            "полностью пустой ответ допустим."
        )
    if encoding == "rank_per_item":
        return [
            {
                "key": name,
                "label": variables[name].get("label") or name,
                "ranks": numeric[name].where(valid),
            }
            for name in sources
        ]
    return [
        {
            "key": code,
            "label": label,
            "ranks": sum(
                numeric[name].eq(code).astype(float) * rank for rank, name in enumerate(sources, 1)
            ).where(valid),
        }
        for code, label in sorted(labels.items())
    ]


def _missing_code(code: float, variable: dict[str, Any], question: dict[str, Any]) -> bool:
    if code in question.get("not_applicable_values", []):
        return True
    for span in variable.get("missing_ranges", []):
        if float(span["lo"]) <= code <= float(span["hi"]):
            return True
    return False
