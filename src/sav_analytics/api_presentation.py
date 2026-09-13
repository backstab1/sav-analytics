"""Производные поля проекта, которые добавляются только в ответ API.

Роутеры возвращают словарь проекта прямо из репозитория, а репозиторий пишет
тот же словарь на диск и хэширует в ключ кэша. Вычисляемый признак, положенный
в него, стал бы сохраняемым полем — ровно тем, от чего отказывается
`core/review.py`. Поэтому признак добавляется на выходе, в одном месте:
маршрут оборачивает эндпоинт и дополняет всякий возвращённый проект.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from fastapi.routing import APIRoute

from .core.review import with_review_state


def present(result: Any) -> Any:
    if isinstance(result, dict) and isinstance(result.get("configuration"), dict):
        return with_review_state(result)
    return result


class ProjectRoute(APIRoute):
    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        if inspect.iscoroutinefunction(endpoint):

            @wraps(endpoint)
            async def async_presented(*args: Any, **inner: Any) -> Any:
                return present(await endpoint(*args, **inner))

            wrapped: Callable[..., Any] = async_presented
        else:

            @wraps(endpoint)
            def presented(*args: Any, **inner: Any) -> Any:
                return present(endpoint(*args, **inner))

            wrapped = presented
        super().__init__(path, wrapped, **kwargs)


__all__ = ["ProjectRoute", "present"]
