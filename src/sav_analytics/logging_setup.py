"""Журнал приложения (P6): строка JSON на событие или обычный текст.

`SAV_ANALYTICS_LOG_FORMAT=json` — для сборщика логов в production: время,
уровень, логгер, сообщение и поля `extra` (request_id, job_id, project_id),
по которым связываются запрос, задание и проект. Строк респондентов и
секретов в журнал не пишут вызывающие (`architecture.md` §5).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                plain = isinstance(value, str | int | float | bool | None)
                entry[key] = value if plain else str(value)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


_configured = False


def configure_logging(settings: Any) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root = logging.getLogger("sav_analytics")
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    root.propagate = False
