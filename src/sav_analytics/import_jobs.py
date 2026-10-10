"""Импорт SAV фоновым заданием (P5): HTTP-запрос только принимает файл,
разбор метаданных и распознавание структуры идут в очереди со стадиями."""

from __future__ import annotations

from typing import Any

from .core.sav_reader import SavReadError
from .jobs.runtime import JobContext, JobFailure, register
from .repository import InvalidUploadError

KIND = "project.import"


@register(KIND, failure_message="Файл не удалось разобрать. Проверьте его и загрузите снова.",
          failure_code="IMPORT_FAILED")
def _import(context: JobContext) -> dict[str, Any]:
    payload = context.payload
    try:
        project = context.repository.create_from_staged(
            payload["name"], payload["staged"], progress=context.progress
        )
    except (InvalidUploadError, SavReadError) as exc:
        raise JobFailure(str(exc), "IMPORT_FAILED") from exc
    return {"project_id": project["id"], "name": project["name"]}
