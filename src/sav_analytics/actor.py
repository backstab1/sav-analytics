"""Кто выполняет действие: пользователь запроса или автор задания в worker.

Контекстная переменная, а не параметр каждого метода: автор нужен в самом
низу — при записи проекта, версии отчёта и журнала — и иначе протягивался
бы через все слои. Без авторизации (локальный прототип) актёра нет.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    id: str
    username: str
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


_actor: ContextVar[Actor | None] = ContextVar("actor", default=None)


def current_actor() -> Actor | None:
    return _actor.get()


def current_actor_id() -> str | None:
    actor = _actor.get()
    return actor.id if actor else None


def bind_actor(actor: Actor | None) -> Token[Actor | None]:
    return _actor.set(actor)


def reset_actor(token: Token[Actor | None]) -> None:
    _actor.reset(token)
