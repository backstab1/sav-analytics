"""Стеммер Snowball для русского языка — локально, без внешних библиотек.

Реализация алгоритма Мартина Портера (snowballstem.org/algorithms/russian).
Нужен кодированию открытых ответов: запрос «доволен» должен находить
«довольна» и «довольны». Стеммер не лемматизатор — он отрезает окончания по
правилам, поэтому совпадение основ проверяется префиксом (см. open_text).
"""

from __future__ import annotations

import re

_VOWELS = "аеиоуыэюя"

_PERFECTIVE_GERUND_1 = ("вшись", "вши", "в")  # после а/я
_PERFECTIVE_GERUND_2 = ("ывшись", "ившись", "ывши", "ивши", "ыв", "ив")
_ADJECTIVE = (
    "ими", "ыми", "его", "ого", "ему", "ому", "ее", "ие", "ые", "ое", "ей", "ий",
    "ый", "ой", "ем", "им", "ым", "ом", "их", "ых", "ую", "юю", "ая", "яя", "ою", "ею",
)
_PARTICIPLE_1 = ("ем", "нн", "вш", "ющ", "щ")  # после а/я
_PARTICIPLE_2 = ("ивш", "ывш", "ующ")
_REFLEXIVE = ("ся", "сь")
_VERB_1 = (
    "ете", "йте", "ешь", "нно", "ла", "на", "ли", "ем", "ло", "но", "ет", "ют", "ны",
    "ть", "й", "л", "н",
)  # после а/я
_VERB_2 = (
    "уйте", "ейте", "ила", "ыла", "ена", "ите", "или", "ыли", "ило", "ыло", "ено", "ует",
    "уют", "ены", "ить", "ыть", "ишь", "ей", "уй", "ил", "ыл", "им", "ым", "ен", "ят",
    "ит", "ыт", "ую", "ю",
)
_NOUN = (
    "иями", "ями", "ами", "ией", "иям", "ием", "иях", "ев", "ов", "ие", "ье", "еи", "ии",
    "ей", "ой", "ий", "ям", "ем", "ам", "ом", "ах", "ях", "ию", "ью", "ия", "ья", "а",
    "е", "и", "й", "о", "у", "ы", "ь", "ю", "я",
)
_SUPERLATIVE = ("ейше", "ейш")
_DERIVATIONAL = ("ость", "ост")


def _longest(endings: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(endings, key=len, reverse=True))


_PERFECTIVE_GERUND_1 = _longest(_PERFECTIVE_GERUND_1)
_PERFECTIVE_GERUND_2 = _longest(_PERFECTIVE_GERUND_2)
_ADJECTIVE = _longest(_ADJECTIVE)
_PARTICIPLE_1 = _longest(_PARTICIPLE_1)
_PARTICIPLE_2 = _longest(_PARTICIPLE_2)
_VERB_1 = _longest(_VERB_1)
_VERB_2 = _longest(_VERB_2)
_NOUN = _longest(_NOUN)


def _regions(word: str) -> tuple[int, int]:
    """Начала RV и R2 (индексы в слове)."""
    rv = len(word)
    for index, char in enumerate(word):
        if char in _VOWELS:
            rv = index + 1
            break
    r1 = len(word)
    for index in range(1, len(word)):
        if word[index] not in _VOWELS and word[index - 1] in _VOWELS:
            r1 = index + 1
            break
    r2 = len(word)
    for index in range(r1 + 1, len(word)):
        if word[index] not in _VOWELS and word[index - 1] in _VOWELS:
            r2 = index + 1
            break
    return rv, r2


def _strip(
    rv_part: str, group_after_a: tuple[str, ...], group_plain: tuple[str, ...]
) -> str | None:
    """Снять самое длинное окончание; группа 1 — только после «а» или «я»."""
    candidates = [(ending, True) for ending in group_after_a] + [
        (ending, False) for ending in group_plain
    ]
    candidates.sort(key=lambda item: len(item[0]), reverse=True)
    for ending, needs_a in candidates:
        if rv_part.endswith(ending):
            stem = rv_part[: -len(ending)]
            if needs_a:
                if stem.endswith(("а", "я")):
                    return stem
                continue
            return stem
    return None


def _strip_plain(rv_part: str, group: tuple[str, ...]) -> str | None:
    for ending in group:
        if rv_part.endswith(ending):
            return rv_part[: -len(ending)]
    return None


def stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    if not re.fullmatch(r"[а-я]+", word):
        return word
    rv, r2 = _regions(word)
    prefix, part = word[:rv], word[rv:]

    # Шаг 1.
    stripped = _strip(part, _PERFECTIVE_GERUND_1, _PERFECTIVE_GERUND_2)
    if stripped is not None:
        part = stripped
    else:
        reflexive = _strip_plain(part, _REFLEXIVE)
        if reflexive is not None:
            part = reflexive
        adjective = _strip_plain(part, _ADJECTIVE)
        if adjective is not None:
            part = adjective
            participle = _strip(part, _PARTICIPLE_1, _PARTICIPLE_2)
            if participle is not None:
                part = participle
        else:
            verb = _strip(part, _VERB_1, _VERB_2)
            if verb is not None:
                part = verb
            else:
                noun = _strip_plain(part, _NOUN)
                if noun is not None:
                    part = noun

    # Шаг 2.
    if part.endswith("и"):
        part = part[:-1]

    # Шаг 3: словообразовательное окончание в R2.
    word_now = prefix + part
    r2_index = r2
    for ending in _DERIVATIONAL:
        if word_now.endswith(ending) and len(word_now) - len(ending) >= r2_index:
            part = part[: -len(ending)]
            break

    # Шаг 4.
    if part.endswith("нн"):
        part = part[:-1]
    else:
        superlative = _strip_plain(part, _SUPERLATIVE)
        if superlative is not None:
            part = superlative
            if part.endswith("нн"):
                part = part[:-1]
        elif part.endswith("ь"):
            part = part[:-1]
    return prefix + part


__all__ = ["stem"]
