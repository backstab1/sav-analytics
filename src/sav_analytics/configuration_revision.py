from __future__ import annotations

from contextvars import ContextVar, Token


class ConfigurationConflictError(RuntimeError):
    """Raised when a client tries to write an outdated project revision."""


_expected_revision: ContextVar[int | None] = ContextVar(
    "expected_configuration_revision", default=None
)


def current_expected_revision() -> int | None:
    return _expected_revision.get()


def bind_expected_revision(revision: int | None) -> Token[int | None]:
    return _expected_revision.set(revision)


def reset_expected_revision(token: Token[int | None]) -> None:
    _expected_revision.reset(token)

