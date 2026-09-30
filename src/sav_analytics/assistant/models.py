"""Подключение языковой модели ассистента.

Всё остальное в пакете говорит с моделью через `ChatModel` и свой формат
сообщений, поэтому провайдер меняется здесь и больше нигде. Формат
сообщений — словари:

- `{"role": "user", "content": str}`
- `{"role": "assistant", "content": str | None, "tool_calls": [ToolCall], "extra": {...}}`
- `{"role": "tool", "tool_call_id": str, "name": str, "content": str}`

`extra` — то, что провайдер вернул вместе с ответом и ждёт обратно в
следующем запросе (например, `reasoning_content` рассуждающих моделей
DeepSeek). Остальной код его не читает.

Адаптер один — OpenAI-совместимый протокол chat completions с вызовом
функций. Его понимают Gemini (эндпоинт OpenAI compatibility), DeepSeek,
Qwen, Mistral, YandexGPT и локальные vLLM и Ollama: провайдер выбирается
адресом, ключом и именем модели в настройках.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


class ModelError(RuntimeError):
    """Модель не ответила или ответила не по протоколу."""


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    # Аргументы разбираются здесь; если модель прислала не JSON, словарь
    # пуст, а текст лежит в `raw_arguments` — ошибку увидит сама модель.
    arguments: dict[str, Any]
    raw_arguments: str | None = None


@dataclass(slots=True)
class ModelReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


class ChatModel(Protocol):
    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> ModelReply: ...


class OpenAICompatibleModel:
    """`POST {base_url}/chat/completions` с `tools` и `tool_choice: auto`."""

    # Поля ответа, которые провайдеры просят вернуть в истории как есть.
    _ECHOED_FIELDS = ("reasoning_content",)

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 60.0,
        temperature: float | None = 0.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> ModelReply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *map(_wire_message, messages)],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ],
            "tool_choice": "auto",
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        try:
            response = self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise ModelError(f"Провайдер модели недоступен: {exc}") from exc
        if response.status_code >= 400:
            raise ModelError(
                f"Провайдер модели ответил {response.status_code}: {response.text[:500]}"
            )
        try:
            message = response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("Провайдер модели прислал ответ не по протоколу.") from exc
        return ModelReply(
            content=message.get("content") or None,
            tool_calls=[_parse_call(call) for call in message.get("tool_calls") or []],
            extra={key: message[key] for key in self._ECHOED_FIELDS if message.get(key)},
        )


def _parse_call(call: dict[str, Any]) -> ToolCall:
    function = call.get("function") or {}
    raw = function.get("arguments")
    if isinstance(raw, dict):
        return ToolCall(id=call.get("id") or "", name=function.get("name") or "", arguments=raw)
    try:
        arguments = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        arguments = None
    if not isinstance(arguments, dict):
        return ToolCall(
            id=call.get("id") or "",
            name=function.get("name") or "",
            arguments={},
            raw_arguments=str(raw),
        )
    return ToolCall(id=call.get("id") or "", name=function.get("name") or "", arguments=arguments)


def _wire_message(message: dict[str, Any]) -> dict[str, Any]:
    role = message["role"]
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message["tool_call_id"],
            "content": message["content"],
        }
    if role == "assistant":
        # Пустой ответ без вызовов — пустая строка: `null` без tool_calls
        # отвергают некоторые провайдеры.
        content = message.get("content")
        if content is None and not message.get("tool_calls"):
            content = ""
        wire: dict[str, Any] = {"role": "assistant", "content": content}
        if message.get("tool_calls"):
            wire["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                    },
                }
                for call in message["tool_calls"]
            ]
        wire.update(message.get("extra") or {})
        return wire
    return {"role": role, "content": message["content"]}
