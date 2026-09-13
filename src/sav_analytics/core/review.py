"""Признак «требует проверки» — вычисляемый, а не сохраняемый.

До этого модуля статус и счётчик «Проверить» смотрели на `recognition ==
"auto_review"`, который выставляется только автоматически собранным группам.
Эвристически распознанная шкала при этом несла предупреждение «требует
проверки» и одновременно статус «Готов»: два источника правды об одном факте.

Источник теперь один — неподтверждённые предупреждения распознавания у
включённого в отчёт вопроса. Подтверждением служит сохранение вопроса: оно
переводит `recognition` в `manual`, отдельного поля под подтверждение нет.
Признак не пишется в `project.json` и не попадает в ключ кэша отчёта — его
добавляет к ответу API `api_presentation`, а preflight зовёт ту же функцию.
"""

from __future__ import annotations

from typing import Any

# Распознавание, которое уже не требует взгляда аналитика: метаданные SPSS
# описаны автором массива, `manual` — подтверждено сохранением вопроса.
CONFIRMED_RECOGNITIONS = frozenset({"metadata", "manual"})


def question_needs_review(question: dict[str, Any]) -> bool:
    if not question.get("included_in_report"):
        return False
    if question.get("recognition", "auto") in CONFIRMED_RECOGNITIONS:
        return False
    return bool(question.get("warnings"))


def questions_needing_review(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        question
        for question in project.get("configuration", {}).get("questions", [])
        if question_needs_review(question)
    ]


def with_review_state(project: dict[str, Any]) -> dict[str, Any]:
    """Копия проекта, у каждого вопроса конфигурации которой есть `needs_review`.

    Исходный словарь не меняется: тот же объект репозиторий пишет на диск и
    хэширует в ключ кэша, а производному признаку там не место.
    """
    configuration = project.get("configuration")
    if not isinstance(configuration, dict) or "questions" not in configuration:
        return project
    questions = [
        {**question, "needs_review": question_needs_review(question)}
        for question in configuration["questions"]
    ]
    return {**project, "configuration": {**configuration, "questions": questions}}


__all__ = [
    "CONFIRMED_RECOGNITIONS",
    "question_needs_review",
    "questions_needing_review",
    "with_review_state",
]
