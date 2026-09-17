"""Кодирование открытых ответов: кодификатор, темы по запросам, ручные отметки.

Кодификатор привязан к открытому вопросу. У темы есть название, родитель
(иерархия), запросы и ручные отметки. Запрос — строка слов:

- все слова строки должны встретиться в ответе (И), строки темы — ИЛИ;
- слово ищется со всеми формами: сравниваются основы Snowball, основа слова
  ответа должна начинаться с основы слова запроса («доволен» → «довольна»);
- `слово*` — буквальный префикс без стемминга, `-слово` — исключение.

Ручная отметка человека перекрывает запрос в обе стороны, и у каждой отметки
виден источник — запрос или человек. Поэтому кодирование воспроизводимо:
тот же кодификатор на той же или новой волне даёт тот же результат.

Тема — вариант multiple-response вопроса: столбец `<код>_<номер>` со значением
1 или 0, пустой у тех, кто не ответил. Столбцы досчитываются при чтении
массива (read_project_frame), как формулы, и работают в таблицах, фильтрах и
баннере как обычный вопрос. Родительская тема отмечена, если отмечена она сама
или любая дочерняя.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .russian_stemmer import stem


class CodeframeError(ValueError):
    pass


_TOKEN = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
MIN_STEM = 2


@dataclass(frozen=True)
class QueryTerm:
    text: str
    prefix: bool
    exclude: bool


def parse_query(line: str) -> list[QueryTerm]:
    terms = []
    for raw in line.split():
        exclude = raw.startswith("-")
        body = raw[1:] if exclude else raw
        prefix = body.endswith("*")
        body = body.rstrip("*").lower().replace("ё", "е")
        words = _TOKEN.findall(body)
        if not words:
            continue
        word = words[0]
        terms.append(QueryTerm(word if prefix else stem(word), prefix, exclude))
    if terms and all(term.exclude for term in terms):
        raise CodeframeError(f"В запросе «{line}» только исключения — нечего искать.")
    return terms


def _word_stems(text: Any) -> tuple[list[str], list[str]]:
    words = [word.lower().replace("ё", "е") for word in _TOKEN.findall(str(text))]
    return words, [stem(word) for word in words]


def _term_found(term: QueryTerm, words: list[str], stems: list[str]) -> bool:
    if term.prefix:
        return any(word.startswith(term.text) for word in words)
    if len(term.text) < MIN_STEM:
        return term.text in words
    return any(item.startswith(term.text) for item in stems)


def query_matches(texts: pd.Series, queries: list[str]) -> pd.Series:
    parsed = [terms for terms in (parse_query(line) for line in queries) if terms]
    if not parsed:
        return pd.Series(False, index=texts.index)
    result = []
    for text in texts:
        if _is_empty(text):
            result.append(False)
            continue
        words, stems = _word_stems(text)
        matched = False
        for terms in parsed:
            if all(_term_found(term, words, stems) != term.exclude for term in terms):
                matched = True
                break
        result.append(matched)
    return pd.Series(result, index=texts.index)


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value)) or not str(value).strip()


def answered_mask(texts: pd.Series) -> pd.Series:
    return texts.map(lambda value: not _is_empty(value))


def theme_variable(codeframe: dict[str, Any], theme: dict[str, Any]) -> str:
    return f"{codeframe['code']}_{theme['number']}"


def validate_codeframe(codeframe: dict[str, Any]) -> None:
    themes = codeframe.get("themes", [])
    ids = [theme["id"] for theme in themes]
    if len(ids) != len(set(ids)):
        raise CodeframeError("У тем повторяются идентификаторы.")
    names = [theme["name"].strip().casefold() for theme in themes]
    if not all(names):
        raise CodeframeError("У каждой темы должно быть название.")
    if len(names) != len(set(names)):
        raise CodeframeError("Названия тем не должны повторяться.")
    by_id = {theme["id"]: theme for theme in themes}
    for theme in themes:
        parent = theme.get("parent_id")
        if parent is None:
            continue
        if parent not in by_id:
            raise CodeframeError(f"У темы «{theme['name']}» не найдена родительская тема.")
        if by_id[parent].get("parent_id") is not None:
            raise CodeframeError("Иерархия тем — два уровня: у дочерней темы не бывает детей.")
        if parent == theme["id"]:
            raise CodeframeError("Тема не может быть родителем самой себя.")
    for theme in themes:
        for line in theme.get("queries", []):
            parse_query(line)


def code_answers(
    texts: pd.Series, codeframe: dict[str, Any]
) -> dict[str, dict[str, pd.Series]]:
    """Отметки тем по ответам: значение и источник («query» или «manual»).

    Возвращает по id темы серию 1.0/0.0/NaN и серию источника отметки.
    """
    answered = answered_mask(texts)
    themes = codeframe.get("themes", [])
    own: dict[str, pd.Series] = {}
    sources: dict[str, pd.Series] = {}
    for theme in themes:
        matched = query_matches(texts, theme.get("queries", [])) & answered
        source = pd.Series(np.where(matched, "query", ""), index=texts.index, dtype=object)
        for row, value in (theme.get("manual") or {}).items():
            position = int(row)
            if position not in texts.index or not answered.get(position, False):
                continue
            matched.loc[position] = bool(value)
            source.loc[position] = "manual"
        own[theme["id"]] = matched
        sources[theme["id"]] = source
    result: dict[str, dict[str, pd.Series]] = {}
    for theme in themes:
        marked = own[theme["id"]].copy()
        for child in themes:
            if child.get("parent_id") == theme["id"]:
                marked |= own[child["id"]]
        values = marked.astype(float).where(answered)
        result[theme["id"]] = {"value": values, "source": sources[theme["id"]]}
    return result


def codeframe_columns(texts: pd.Series, codeframe: dict[str, Any]) -> dict[str, pd.Series]:
    coded = code_answers(texts, codeframe)
    return {
        theme_variable(codeframe, theme): coded[theme["id"]]["value"]
        for theme in codeframe.get("themes", [])
    }


def theme_owners(project: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Имя столбца темы → кодификатор, которому он принадлежит."""
    owners = {}
    for codeframe in ((project or {}).get("configuration") or {}).get("codeframes", []):
        for theme in codeframe.get("themes", []):
            owners[theme_variable(codeframe, theme)] = codeframe
    return owners


def codeframe_text_variable(codeframe: dict[str, Any], project: dict[str, Any]) -> str:
    question = next(
        (
            item
            for item in project["configuration"]["questions"]
            if item["code"] == codeframe["question_code"]
        ),
        None,
    )
    if question is None or len(question.get("source_variables") or []) != 1:
        raise CodeframeError("Открытый вопрос кодификатора не найден.")
    return question["source_variables"][0]


def codeframe_summary(texts: pd.Series, codeframe: dict[str, Any]) -> dict[str, Any]:
    coded = code_answers(texts, codeframe)
    answered = answered_mask(texts)
    any_theme = pd.Series(False, index=texts.index)
    counts = []
    for theme in codeframe.get("themes", []):
        values = coded[theme["id"]]["value"]
        source = coded[theme["id"]]["source"]
        marked = values.fillna(0).astype(bool)
        any_theme |= marked
        counts.append(
            {
                "id": theme["id"],
                "count": int(marked.sum()),
                "manual": int((source == "manual").sum()),
                "share": float(marked.sum() / answered.sum()) if answered.sum() else None,
            }
        )
    return {
        "answered": int(answered.sum()),
        "uncoded": int((answered & ~any_theme).sum()),
        "themes": counts,
    }


def answer_rows(
    texts: pd.Series,
    codeframe: dict[str, Any],
    *,
    theme_id: str | None = None,
    uncoded: bool = False,
    search: str = "",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    coded = code_answers(texts, codeframe)
    answered = answered_mask(texts)
    selected = answered.copy()
    if theme_id:
        if theme_id not in coded:
            raise CodeframeError("Тема не найдена.")
        selected &= coded[theme_id]["value"].fillna(0).astype(bool)
    if uncoded:
        any_theme = pd.Series(False, index=texts.index)
        for item in coded.values():
            any_theme |= item["value"].fillna(0).astype(bool)
        selected &= ~any_theme
    if search.strip():
        selected &= query_matches(texts, [search])
    rows = list(texts.index[selected])
    page = rows[offset : offset + limit]
    return {
        "total": len(rows),
        "rows": [
            {
                "row": int(position),
                "text": str(texts.loc[position]),
                "themes": [
                    {
                        "id": theme["id"],
                        "source": coded[theme["id"]]["source"].loc[position] or "child",
                    }
                    for theme in codeframe.get("themes", [])
                    if coded[theme["id"]]["value"].loc[position] == 1
                ],
            }
            for position in page
        ],
    }


_WORD = re.compile(r"[a-zа-яё]{2,}", re.IGNORECASE)
WORDY_SHARE = 0.3


def text_profile(texts: pd.Series) -> dict[str, Any]:
    """Похож ли столбец на ответы респондентов, а не на служебное поле.

    Служебные поля — логин, телефон, дата, идентификатор — формально тоже
    текст. Отличает их содержимое: в ответе обычно хотя бы два слова.
    """
    answered = texts[answered_mask(texts)].astype(str)
    if answered.empty:
        return {"answered": 0, "wordy_share": 0.0, "average_words": 0.0, "example": ""}
    counts = answered.map(lambda value: len(_WORD.findall(value)))
    wordy = counts >= 2
    examples = answered[wordy] if wordy.any() else answered
    example = max(examples.head(200), key=len)
    return {
        "answered": int(answered.size),
        "wordy_share": float(wordy.mean()),
        "average_words": float(counts.mean()),
        "example": example[:120],
    }


_SERVICE = re.compile(
    r"(^|[^a-zа-яё])(id|uid|guid|имя|фамили|логин|login|user|телефон|phone|e-?mail|почт|дата|"
    r"date|время|time|адрес|address|ip|contact|контакт)",
    re.IGNORECASE,
)


def looks_like_service_field(code: str, label: str) -> bool:
    """Код или подпись говорят о служебном поле: идентификатор, имя, контакт, дата."""
    return bool(_SERVICE.search(code) or _SERVICE.search(label))

