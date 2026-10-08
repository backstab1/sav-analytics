"""Атомарная замена файла и чтение, которые переживают друг друга в Windows."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

# В Windows `os.replace` поверх файла, который в этот момент читает другой
# запрос, падает с отказом в доступе (WinError 5): чтение project.json и
# истории идёт без блокировки проекта. Читатель держит файл миллисекунды,
# поэтому замену повторяем с нарастающей паузой — в сумме меньше секунды.
# В POSIX переименование поверх открытого файла разрешено, и первая попытка
# проходит всегда.
_ATTEMPTS = 10
_FIRST_DELAY = 0.005
_MAX_DELAY = 0.2


def replace_file(source: Path, target: Path) -> None:
    _retrying(lambda: os.replace(source, target))


# Обратная сторона той же гонки: открыть файл в тот миг, когда поверх него
# идёт замена, Windows тоже не даёт (Errno 13). Так падал GET или запись,
# читающая project.json, пока соседний запрос того же окна его сохранял.
def read_text(path: Path) -> str:
    return _retrying(lambda: path.read_text(encoding="utf-8"))


def _retrying[T](action: Callable[[], T]) -> T:
    delay = _FIRST_DELAY
    for attempt in range(_ATTEMPTS):
        try:
            return action()
        except PermissionError:
            if attempt == _ATTEMPTS - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, _MAX_DELAY)
    raise AssertionError("unreachable")
