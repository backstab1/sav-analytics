"""Готовность экземпляра к работе (P6): база, схема, том данных."""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any

import sqlalchemy as sa


def readiness(settings: Any) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        from .db.engine import _create_engine, current_revision, head_revision

        engine = _create_engine(settings.resolved_database_url)
        try:
            with engine.connect() as connection:
                connection.execute(sa.text("SELECT 1"))
            current = current_revision(engine)
        finally:
            engine.dispose()
        checks["database"] = "ok"
        checks["schema"] = "ok" if current == head_revision() else f"outdated: {current}"
    except Exception as exc:  # noqa: BLE001 — отчёт, а не падение
        checks["database"] = f"error: {type(exc).__name__}"
        checks["schema"] = "unknown"
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        descriptor, path = tempfile.mkstemp(prefix=".ready-", dir=settings.data_dir)
        os.close(descriptor)
        os.unlink(path)
        free_mb = shutil.disk_usage(settings.data_dir).free // (1024 * 1024)
        checks["storage"] = "ok"
        checks["free_disk_mb"] = free_mb
    except OSError as exc:
        checks["storage"] = f"error: {type(exc).__name__}"
    ready = all(checks.get(key) == "ok" for key in ("database", "schema", "storage"))
    return {"ready": ready, "checks": checks}
