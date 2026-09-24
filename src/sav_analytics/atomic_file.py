"""Атомарная замена файла, которая переживает параллельного читателя в Windows."""

from __future__ import annotations

import os
import time
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
    delay = _FIRST_DELAY
    for attempt in range(_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == _ATTEMPTS - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, _MAX_DELAY)
