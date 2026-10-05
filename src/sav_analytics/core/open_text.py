"""Кодирование открытых ответов (PQ.17, решение 034): справочник кодов и
кодирование ответов моделью с ручной правкой.

Кодификатор привязан к открытому вопросу. Справочник — коды в два уровня:
группа (родитель) и коды внутри неё; группа становится NET. Каждый код —
вариант multiple-response вопроса: столбец `<код>_<номер>` со значением 1
или 0, пустой у тех, кто не ответил. Родитель отмечен, если отмечен он сам
или любой его код. Столбцы досчитываются при чтении массива
(`read_project_frame`) и работают в таблицах, фильтрах и баннере.

Коды ставятся не строке массива, а **тексту ответа**: одинаковые ответы
кодируются одинаково, а правка человека пишется в словарь «текст → коды»
и переходит на новую волну и в другие проекты. Текст сравнивается
нормализованным (`normalize_answer`).

Результат кодирования — ответы, словарь, источники и уверенность — может
занимать мегабайты, поэтому он не живёт в `project.json`, который целиком
уходит в историю отмены и ключ кэша. Он лежит неизменяемым файлом
`coding/<sha>.json` рядом с проектом, а кодификатор хранит ссылку на него
(`coding_ref`). Новая правка — новый файл и новая ссылка: отмена
возвращает прежнюю ссылку, ключ кэша отчёта меняется вместе с ней.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class CodeframeError(ValueError):
    pass


CODING_DIR = "coding"
OTHER_NAME = "Другое"
# Источник кодов ответа: модель, человек, словарь правок прежних волн.
SOURCES = ("ai", "manual", "dictionary")


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value)) or not str(value).strip()


def answered_mask(texts: pd.Series) -> pd.Series:
    return texts.map(lambda value: not _is_empty(value))


def normalize_answer(value: Any) -> str:
    """Текст ответа как ключ: регистр, «ё», пробелы и края не различаются."""
    text = str(value).strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def theme_variable(codeframe: dict[str, Any], theme: dict[str, Any]) -> str:
    return f"{codeframe['code']}_{theme['number']}"


def validate_codeframe(codeframe: dict[str, Any]) -> None:
    themes = codeframe.get("themes", [])
    ids = [theme["id"] for theme in themes]
    if len(ids) != len(set(ids)):
        raise CodeframeError("У кодов повторяются идентификаторы.")
    names = [theme["name"].strip().casefold() for theme in themes]
    if not all(names):
        raise CodeframeError("У каждого кода должно быть название.")
    if len(names) != len(set(names)):
        raise CodeframeError("Названия кодов не должны повторяться.")
    by_id = {theme["id"]: theme for theme in themes}
    for theme in themes:
        parent = theme.get("parent_id")
        if parent is None:
            continue
        if parent == theme["id"]:
            raise CodeframeError("Код не может быть группой самому себе.")
        if parent not in by_id:
            raise CodeframeError(f"У кода «{theme['name']}» не найдена группа.")
        if by_id[parent].get("parent_id") is not None:
            raise CodeframeError("Справочник — два уровня: у кода внутри группы нет своих кодов.")


# ---------------------------------------------------------------- хранение


def empty_coding() -> dict[str, Any]:
    return {"answers": {}, "dictionary": {}}


def store_coding(project_dir: Path, coding: dict[str, Any]) -> str:
    """Записать результат кодирования неизменяемым файлом и вернуть ссылку."""
    data = json.dumps(coding, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ref = hashlib.sha256(data.encode("utf-8")).hexdigest()[:32]
    directory = project_dir / CODING_DIR
    directory.mkdir(exist_ok=True)
    target = directory / f"{ref}.json"
    if not target.exists():
        temporary = directory / f".{ref}.tmp"
        temporary.write_text(data, encoding="utf-8")
        temporary.replace(target)
    return ref


def load_coding(project_dir: Path, ref: str | None) -> dict[str, Any]:
    if not ref:
        return empty_coding()
    return json.loads(json.dumps(_cached_coding(str(project_dir), ref)))


@lru_cache(maxsize=64)
def _cached_coding(project_dir: str, ref: str) -> dict[str, Any]:
    path = Path(project_dir) / CODING_DIR / f"{ref}.json"
    if not path.is_file():
        # Файл потерян (ручная чистка папки): кодирование пустое, а не ошибка
        # чтения всего массива.
        return empty_coding()
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- расчёт


def answer_codes(
    codeframe: dict[str, Any], coding: dict[str, Any], key: str
) -> list[str]:
    """Коды ответа: словарь правок важнее кодов модели."""
    known = {theme["id"] for theme in codeframe.get("themes", [])}
    if key in coding.get("dictionary", {}):
        codes = coding["dictionary"][key]
    else:
        codes = (coding.get("answers", {}).get(key) or {}).get("codes", [])
    return [code for code in codes if code in known]


def answer_source(coding: dict[str, Any], key: str) -> str | None:
    if key in coding.get("dictionary", {}):
        entry = coding.get("answers", {}).get(key) or {}
        return "manual" if entry.get("source") == "manual" else "dictionary"
    entry = coding.get("answers", {}).get(key)
    return entry.get("source") if entry else None


def code_answers(
    texts: pd.Series, codeframe: dict[str, Any], coding: dict[str, Any]
) -> dict[str, pd.Series]:
    """Отметки кодов по строкам: id кода → серия 1.0/0.0/NaN."""
    answered = answered_mask(texts)
    keys = texts.map(lambda value: None if _is_empty(value) else normalize_answer(value))
    themes = codeframe.get("themes", [])
    own: dict[str, list[bool]] = {theme["id"]: [] for theme in themes}
    cache: dict[str, set[str]] = {}
    for key in keys:
        if key is None:
            codes: set[str] = set()
        else:
            if key not in cache:
                cache[key] = set(answer_codes(codeframe, coding, key))
            codes = cache[key]
        for theme in themes:
            own[theme["id"]].append(theme["id"] in codes)
    result: dict[str, pd.Series] = {}
    for theme in themes:
        marked = pd.Series(own[theme["id"]], index=texts.index, dtype=bool)
        for child in themes:
            if child.get("parent_id") == theme["id"]:
                marked |= pd.Series(own[child["id"]], index=texts.index, dtype=bool)
        result[theme["id"]] = marked.astype(float).where(answered)
    return result


def codeframe_columns(
    texts: pd.Series, codeframe: dict[str, Any], coding: dict[str, Any]
) -> dict[str, pd.Series]:
    coded = code_answers(texts, codeframe, coding)
    return {
        theme_variable(codeframe, theme): coded[theme["id"]]
        for theme in codeframe.get("themes", [])
    }


def theme_owners(project: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Имя столбца кода → кодификатор, которому он принадлежит."""
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


def unique_answers(texts: pd.Series) -> list[tuple[str, str, int]]:
    """Уникальные ответы по убыванию частоты: ключ, пример текста, число."""
    counts: dict[str, int] = {}
    examples: dict[str, str] = {}
    for value in texts:
        if _is_empty(value):
            continue
        key = normalize_answer(value)
        counts[key] = counts.get(key, 0) + 1
        examples.setdefault(key, str(value).strip())
    return sorted(
        ((key, examples[key], count) for key, count in counts.items()),
        key=lambda item: (-item[2], item[0]),
    )


def codeframe_summary(
    texts: pd.Series, codeframe: dict[str, Any], coding: dict[str, Any]
) -> dict[str, Any]:
    """Плитки ревью и счётчики кодов."""
    answered = answered_mask(texts)
    unique = unique_answers(texts)
    tiles = {"unique": len(unique), "dictionary": 0, "ai": 0, "low": 0, "uncoded": 0}
    uncoded_rows = 0
    for key, _text, count in unique:
        codes = answer_codes(codeframe, coding, key)
        source = answer_source(coding, key)
        if source in {"manual", "dictionary"}:
            tiles["dictionary"] += 1
        elif source == "ai":
            tiles["ai"] += 1
            if (coding["answers"].get(key) or {}).get("low"):
                tiles["low"] += 1
        if not codes:
            tiles["uncoded"] += 1
            uncoded_rows += count
    coded = code_answers(texts, codeframe, coding)
    total = int(answered.sum())
    themes = []
    for theme in codeframe.get("themes", []):
        marked = coded[theme["id"]].fillna(0).astype(bool)
        themes.append(
            {
                "id": theme["id"],
                "count": int(marked.sum()),
                "share": float(marked.sum() / total) if total else None,
            }
        )
    return {
        "answered": total,
        "uncoded": uncoded_rows,
        "tiles": tiles,
        "themes": themes,
    }


def answer_rows(
    texts: pd.Series,
    codeframe: dict[str, Any],
    coding: dict[str, Any],
    *,
    theme_id: str | None = None,
    view: str = "all",
    search: str = "",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Уникальные ответы с кодами и источником, по фильтру ревью.

    `view`: all, uncoded (без кода), low (низкая уверенность модели),
    dictionary (из словаря правок), ai (поставлены моделью).
    """
    known = {theme["id"]: theme for theme in codeframe.get("themes", [])}
    if theme_id and theme_id not in known:
        raise CodeframeError("Код не найден.")
    needle = normalize_answer(search) if search.strip() else ""
    rows = []
    for key, text, count in unique_answers(texts):
        codes = answer_codes(codeframe, coding, key)
        source = answer_source(coding, key)
        low = bool((coding.get("answers", {}).get(key) or {}).get("low")) and source == "ai"
        if view == "uncoded" and codes:
            continue
        if view == "low" and not low:
            continue
        if view == "dictionary" and source not in {"manual", "dictionary"}:
            continue
        if view == "ai" and source != "ai":
            continue
        if theme_id:
            children = {
                item["id"] for item in known.values() if item.get("parent_id") == theme_id
            }
            if not ({theme_id} | children) & set(codes):
                continue
        if needle and needle not in key:
            continue
        rows.append(
            {"key": key, "text": text, "count": count, "codes": codes, "source": source,
             "low": low}
        )
    return {"total": len(rows), "rows": rows[offset : offset + limit]}


def merge_rare_codes(
    themes: list[dict[str, Any]],
    coding: dict[str, Any],
    counts: dict[str, int],
    total: int,
    threshold: float,
    new_id: Any,
    next_number: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    """Коды реже порога уходят в «Другое» (решение 034).

    Сравнивается доля ответивших с кодом; группы не сливаются, а коды
    внутри группы сливаются в общее «Другое». Возвращает справочник,
    кодирование и следующий свободный номер.
    """
    if threshold <= 0 or not total:
        return themes, coding, next_number
    parents = {theme.get("parent_id") for theme in themes if theme.get("parent_id")}
    rare = {
        theme["id"]
        for theme in themes
        if theme["id"] not in parents
        and theme["name"].casefold() != OTHER_NAME.casefold()
        and counts.get(theme["id"], 0) / total < threshold
    }
    if not rare:
        return themes, coding, next_number
    other = next(
        (theme for theme in themes if theme["name"].casefold() == OTHER_NAME.casefold()), None
    )
    kept = [theme for theme in themes if theme["id"] not in rare]
    if other is None:
        other = {"id": str(new_id()), "number": next_number, "name": OTHER_NAME,
                 "parent_id": None, "description": "Редкие ответы"}
        next_number += 1
        kept.append(other)
    for entry in coding["answers"].values():
        codes = entry.get("codes", [])
        if rare & set(codes):
            entry["codes"] = list(dict.fromkeys(
                [code for code in codes if code not in rare] + [other["id"]]
            ))
    return kept, coding, next_number


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
