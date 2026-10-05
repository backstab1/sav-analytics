"""Разбор анкеты моделью: один запрос, результат — вызов `submit_mapping`.

Модель только предлагает. Что из предложенного применимо, решает
`core.questionnaire.proposal_rows`, что применить — человек по таблице
«было → станет».
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ChatModel, ModelError, ToolSpec

SYSTEM_PROMPT = (Path(__file__).with_name("questionnaire_prompt.md")).read_text(encoding="utf-8")

_STRING = {"type": "string"}

SUBMIT_MAPPING = ToolSpec(
    name="submit_mapping",
    description="Сдать сопоставление анкеты с массивом.",
    parameters={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": _STRING,
                        "label": _STRING,
                        "question_type": {
                            "type": "string",
                            "enum": ["single_choice", "scale", "numeric", "open_text"],
                        },
                    },
                    "required": ["code"],
                },
            },
            "variables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": _STRING,
                        "label": _STRING,
                        "values": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"value": _STRING, "label": _STRING},
                                "required": ["value", "label"],
                            },
                        },
                    },
                    "required": ["name"],
                },
            },
            "order": {"type": "array", "items": _STRING},
            "notes": _STRING,
        },
        "required": ["questions", "variables"],
    },
)


def request_mapping(
    model: ChatModel, text: str, catalog: list[dict[str, Any]], *, truncated: bool
) -> dict[str, Any]:
    """Попросить модель сопоставить анкету и вернуть аргументы `submit_mapping`.

    Если модель ответила текстом без вызова, её один раз просят сдать
    результат вызовом.
    """
    note = (
        "\n\n[Текст анкеты обрезан: дальше были приложения или продолжение анкеты.]"
        if truncated
        else ""
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"<анкета>\n{text}{note}\n</анкета>\n\n"
                f"<каталог_массива>\n{json.dumps(catalog, ensure_ascii=False)}\n"
                "</каталог_массива>"
            ),
        }
    ]
    for _attempt in range(2):
        reply = model.complete(SYSTEM_PROMPT, messages, [SUBMIT_MAPPING])
        call = next((item for item in reply.tool_calls if item.name == SUBMIT_MAPPING.name), None)
        if call is not None and call.raw_arguments is None:
            return call.arguments
        messages.append(
            {
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {"id": item.id, "name": item.name, "arguments": item.arguments}
                    for item in reply.tool_calls
                ],
                "extra": reply.extra,
            }
        )
        for item in reply.tool_calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.id,
                    "name": item.name,
                    "content": "Аргументы не разобраны: нужен JSON по схеме submit_mapping.",
                }
            )
        messages.append(
            {"role": "user", "content": "Сдай результат одним вызовом submit_mapping."}
        )
    raise ModelError("Модель не вернула сопоставление анкеты.")
