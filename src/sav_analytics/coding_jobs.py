"""Фоновые задачи кодирования открытых ответов (PQ.17).

Режимы перекодирования (решение 034):

- `new` — только ответы, у которых ещё нет кодов (новая волна, докодировать);
- `keep_edits` — всё заново моделью, кроме ответов из словаря правок;
- `reset` — сбросить справочник и словарь, построить справочник и
  закодировать всё с нуля.

Пустой справочник строится основной моделью по выборке ответов; коды
ставит модель массовых задач пачками, по четыре пачки параллельно.
При включённой тональности тем же вызовом ставится тон. Ответ, у которого
коды уже есть (из словаря правок или с прошлого запуска в режиме `new`),
а тона нет, уходит модели только за тоном: его коды не меняются. Пачка,
которая не удалась дважды, остаётся незакодированной — это видно на
плитке «без кода», а не роняет всю задачу.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

from .ai_jobs import JobFailure, Progress
from .assistant.coding import request_codebook, request_codes, request_revision
from .assistant.models import ChatModel, ModelError
from .core.formulas import read_project_frame
from .core.open_text import (
    OTHER_NAME,
    TONE_VALUES,
    answer_codes,
    answer_tone,
    codeframe_text_variable,
    empty_coding,
    merge_rare_codes,
    unique_answers,
)
from .repository import ProjectNotFoundError, ProjectRepository

BATCH_SIZE = 60
PARALLEL_BATCHES = 4
SAMPLE_TOP = 250
SAMPLE_TOTAL = 400
MODES = ("new", "keep_edits", "reset")


def _context(
    repository: ProjectRepository, project_id: UUID, codeframe_id: UUID
) -> tuple[dict, dict, list[tuple[str, str, int]], str]:
    try:
        project = repository.get(project_id)
        codeframe = repository._find_codeframe(project, codeframe_id)
    except ProjectNotFoundError as exc:
        raise JobFailure("Кодификатор или проект удалён.") from exc
    variable = codeframe_text_variable(codeframe, project)
    texts = read_project_frame(
        repository.source_path(project_id), project, [variable], all_waves=True
    )[variable]
    question = next(
        item for item in project["configuration"]["questions"]
        if item["code"] == codeframe["question_code"]
    )
    return project, codeframe, unique_answers(texts), question["label"]


def sample_answers(unique: list[tuple[str, str, int]]) -> list[tuple[str, int]]:
    """Выборка для справочника: самые частые и равномерно — остальные."""
    top = unique[:SAMPLE_TOP]
    rest = unique[SAMPLE_TOP:]
    room = SAMPLE_TOTAL - len(top)
    if rest and room > 0:
        step = max(1, len(rest) // room)
        top = top + rest[::step][:room]
    return [(text, count) for _key, text, count in top]


def codebook_themes(
    proposal: dict[str, Any],
    existing: list[dict[str, Any]],
    next_number: int,
) -> tuple[list[dict[str, Any]], int]:
    """Справочник модели → коды проекта.

    Группа становится родительским кодом. `keep` сохраняет прежний код
    (его id и номер), чтобы ответы с ним остались закодированными.
    Повторы названий отбрасываются.
    """
    by_number = {theme["number"]: theme for theme in existing}
    by_name = {theme["name"].casefold(): theme for theme in existing}
    themes: list[dict[str, Any]] = []
    used: set[str] = set()
    groups: dict[str, dict[str, Any]] = {}

    def take(name: str, description: str, keep: Any, parent: str | None) -> dict | None:
        nonlocal next_number
        name = " ".join(name.split())[:250]
        if not name or name.casefold() in used:
            return None
        previous = by_number.get(keep) if isinstance(keep, int) else None
        if previous is None and keep is None:
            previous = by_name.get(name.casefold())
        if previous is not None and previous["id"] in {theme["id"] for theme in themes}:
            previous = None
        if previous is None:
            record = {"id": str(uuid4()), "number": next_number}
            next_number += 1
        else:
            record = {"id": previous["id"], "number": previous["number"]}
        record.update(name=name, description=description[:500], parent_id=parent)
        used.add(name.casefold())
        themes.append(record)
        return record

    codes = [item for item in proposal.get("codes") or [] if isinstance(item, dict)]
    group_sizes: dict[str, int] = {}
    for item in codes:
        group = " ".join(str(item.get("group") or "").split())
        if group:
            group_sizes[group.casefold()] = group_sizes.get(group.casefold(), 0) + 1
    for item in codes:
        group = " ".join(str(item.get("group") or "").split())
        parent_id = None
        if group and group_sizes.get(group.casefold(), 0) >= 2:
            if group.casefold() not in groups:
                record = take(group, "", None, None)
                if record is None:
                    record = next(
                        theme for theme in themes if theme["name"].casefold() == group.casefold()
                    )
                groups[group.casefold()] = record
            parent_id = groups[group.casefold()]["id"]
        take(str(item.get("name") or ""), str(item.get("description") or ""), item.get("keep"),
             parent_id)
    if not themes:
        raise JobFailure("Модель вернула пустой справочник кодов.")
    return themes, next_number


def leaves(themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parents = {theme.get("parent_id") for theme in themes if theme.get("parent_id")}
    return [theme for theme in themes if theme["id"] not in parents]


def run_coding(
    repository: ProjectRepository,
    project_id: UUID,
    codeframe_id: UUID,
    mode: str,
    main_model: ChatModel,
    fast_model: ChatModel,
    progress: Progress,
) -> dict[str, Any]:
    progress(0, 1, "Собираем ответы")
    _project, codeframe, unique, question = _context(repository, project_id, codeframe_id)
    if not unique:
        raise JobFailure("У вопроса нет ни одного ответа.")
    themes = [dict(theme) for theme in codeframe["themes"]]
    next_number = codeframe.get("next_number", 1)
    coding = repository.coding(project_id, codeframe)
    if mode == "reset":
        coding = empty_coding()
        themes = []
    generated = False
    if not themes:
        progress(0, 1, "Модель строит справочник кодов")
        try:
            proposal = request_codebook(
                main_model, question, codeframe.get("instruction") or "", sample_answers(unique)
            )
        except ModelError as exc:
            raise JobFailure(f"Модель не ответила: {exc}", "AI_PROVIDER_ERROR") from exc
        themes, next_number = codebook_themes(proposal, [], next_number)
        generated = True
    targets = leaves(themes)
    by_number = {theme["number"]: theme["id"] for theme in targets}
    dictionary = coding["dictionary"]
    sentiment = bool(codeframe.get("sentiment"))
    coding.setdefault("tones", {})

    def needs_tone(key: str) -> bool:
        return sentiment and answer_tone(coding, key) is None

    if mode == "new":
        todo = [
            (key, text) for key, text, _count in unique
            if (key not in dictionary and key not in coding["answers"]) or needs_tone(key)
        ]
        tone_only = {key for key, _text in todo if key in dictionary or key in coding["answers"]}
    else:
        todo = [(key, text) for key, text, _count in unique if key not in dictionary]
        for key, _text in todo:
            coding["answers"].pop(key, None)
        todo += [
            (key, text) for key, text, _count in unique if key in dictionary and needs_tone(key)
        ]
        tone_only = {key for key, _text in todo if key in dictionary}
    batches = [todo[start : start + BATCH_SIZE] for start in range(0, len(todo), BATCH_SIZE)]
    multi = bool(codeframe.get("multi", True))
    done = 0
    failed = 0
    failed_answers = 0
    progress(0, max(1, len(batches)), f"Кодируем ответы: 0 из {len(todo)}")

    def code_batch(batch: list[tuple[str, str]]) -> dict[str, dict[str, Any]] | None:
        for _attempt in range(2):
            try:
                result = request_codes(
                    fast_model, question, codeframe.get("instruction") or "", targets,
                    [text for _key, text in batch], multi=multi, sentiment=sentiment,
                )
            except ModelError:
                continue
            coded: dict[str, dict[str, Any]] = {}
            for item in result.get("items") or []:
                if not isinstance(item, dict) or not isinstance(item.get("i"), int):
                    continue
                if not 0 <= item["i"] < len(batch):
                    continue
                codes = [by_number[number] for number in item.get("codes") or []
                         if isinstance(number, int) and number in by_number]
                codes = list(dict.fromkeys(codes))
                if not multi:
                    codes = codes[:1]
                entry: dict[str, Any] = {
                    "codes": codes,
                    "source": "ai",
                    "low": bool(item.get("low_confidence")) or not codes,
                }
                if sentiment and item.get("tone") in TONE_VALUES:
                    entry["tone"] = item["tone"]
                coded[batch[item["i"]][0]] = entry
            for key, _text in batch:
                # Пропущенный моделью ответ — без кода и на проверку человеку.
                if key not in tone_only:
                    coded.setdefault(key, {"codes": [], "source": "ai", "low": True})
            return coded
        return None

    with ThreadPoolExecutor(max_workers=PARALLEL_BATCHES) as pool:
        for batch, result in zip(batches, pool.map(code_batch, batches), strict=True):
            done += 1
            if result is None:
                failed += 1
                failed_answers += len(batch)
            else:
                for key, entry in result.items():
                    if key in tone_only:
                        # Коды ответа уже стоят — берётся только тон.
                        if "tone" in entry:
                            coding["answers"].setdefault(key, {})["tone"] = entry["tone"]
                    else:
                        coding["answers"][key] = entry
            progress(done, len(batches), f"Кодируем ответы: {min(done * BATCH_SIZE, len(todo))} "
                     f"из {len(todo)}")
    if batches and failed == len(batches):
        raise JobFailure("Модель не закодировала ни одной пачки ответов.", "AI_PROVIDER_ERROR")
    if generated:
        counts = {theme["id"]: 0 for theme in themes}
        total = 0
        for key, _text, count in unique:
            total += count
            for code in answer_codes({"themes": themes}, coding, key):
                counts[code] += count
        themes, coding, next_number = merge_rare_codes(
            themes, coding, counts, total, float(codeframe.get("other_threshold") or 0),
            uuid4, next_number,
        )
    try:
        repository.save_coding_result(project_id, codeframe_id, themes, next_number, coding)
    except ProjectNotFoundError as exc:
        raise JobFailure("Кодификатор удалён во время кодирования.") from exc
    return {
        "codeframe_id": str(codeframe_id),
        "coded": len(todo) - failed_answers,
        "failed_batches": failed,
        "generated": generated,
        "codes": len(themes),
        "other": any(theme["name"] == OTHER_NAME for theme in themes),
    }


def run_revision(
    repository: ProjectRepository,
    project_id: UUID,
    codeframe_id: UUID,
    request: str,
    main_model: ChatModel,
    progress: Progress,
) -> dict[str, Any]:
    """Правка справочника моделью — черновик, который человек сохранит сам."""
    progress(0, 1, "Модель правит справочник")
    _project, codeframe, unique, question = _context(repository, project_id, codeframe_id)
    try:
        proposal = request_revision(
            main_model, question, codeframe["themes"], request, sample_answers(unique)[:200]
        )
    except ModelError as exc:
        raise JobFailure(f"Модель не ответила: {exc}", "AI_PROVIDER_ERROR") from exc
    themes, _next = codebook_themes(proposal, codeframe["themes"], codeframe.get("next_number", 1))
    kept = {theme["id"] for theme in codeframe["themes"]}
    return {
        "codeframe_id": str(codeframe_id),
        # Новые коды получают временный id экрана: постоянный выдаст сохранение.
        "themes": [
            {**theme, "id": theme["id"] if theme["id"] in kept else f"new-{theme['id']}",
             "parent_id": (theme["parent_id"] if not theme.get("parent_id")
                           or theme["parent_id"] in kept else f"new-{theme['parent_id']}")}
            for theme in themes
        ],
        "notes": str(proposal.get("notes") or "")[:2000],
    }


JobRunner = Callable[[Progress], dict[str, Any]]
__all__ = ["MODES", "codebook_themes", "run_coding", "run_revision", "sample_answers"]
