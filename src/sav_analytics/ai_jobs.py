"""Фоновые задачи ИИ: разбор анкеты, позже — кодирование и автоотчёт.

Задача живёт в памяти процесса, как сборка отчёта в `report_jobs.py`:
надёжной очереди с переживанием перезапуска пока нет (P5). Задача знает
проект, вид, прогресс, результат или ошибку и умеет повториться с теми же
входными данными — для этого она держит функцию и её аргументы.

Функция задачи получает `progress(completed, total, stage)` и возвращает
словарь результата. `JobFailure` — ожидаемая ошибка с текстом для человека;
любое другое исключение пишется в журнал и показывается общим текстом.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

JobStatus = Literal["queued", "running", "complete", "failed"]
Progress = Callable[[int, int, str], None]
JobFunction = Callable[[Progress], dict[str, Any]]

# Две задачи сразу: разбор анкеты не должен ждать кодирования большого массива.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-job")
_guard = threading.Lock()
_jobs: dict[str, AiJob] = {}
# Сколько завершённых задач проекта помнить для колокольчика.
_KEEP_FINISHED = 20
logger = logging.getLogger(__name__)


class JobFailure(RuntimeError):
    """Ожидаемая ошибка задачи: текст показывается человеку как есть."""

    def __init__(self, message: str, code: str = "AI_JOB_FAILED") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class AiJob:
    id: str
    project_id: str
    kind: str
    title: str
    function: JobFunction = field(repr=False)
    status: JobStatus = "queued"
    completed: int = 0
    total: int = 1
    stage: str = "В очереди"
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    attempts: int = 0

    def payload(self, *, with_result: bool = True) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "project_id": self.project_id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "completed": self.completed,
            "total": self.total,
            "progress": round(self.completed / self.total * 100) if self.total else 0,
            "stage": self.stage,
            "error": self.error,
            "error_code": self.error_code,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "attempts": self.attempts,
            "result": self.result if with_result else None,
        }


def start_job(project_id: str, kind: str, title: str, function: JobFunction) -> dict[str, Any]:
    job = AiJob(id=str(uuid4()), project_id=project_id, kind=kind, title=title, function=function)
    with _guard:
        _jobs[job.id] = job
        _forget_old(project_id)
    _submit(job)
    return job.payload()


def retry_job(project_id: str, job_id: str) -> dict[str, Any] | None:
    """Повторить упавшую задачу с теми же входными данными."""
    with _guard:
        job = _jobs.get(job_id)
        if job is None or job.project_id != project_id:
            return None
        if job.status != "failed":
            return job.payload()
        job.status = "queued"
        job.stage = "В очереди"
        job.completed = 0
        job.error = None
        job.error_code = None
        job.finished_at = None
    _submit(job)
    return job.payload()


def get_job(project_id: str, job_id: str) -> dict[str, Any] | None:
    with _guard:
        job = _jobs.get(job_id)
        if job is None or job.project_id != project_id:
            return None
        return job.payload()


def job_result(project_id: str, job_id: str, kind: str) -> dict[str, Any] | None:
    """Результат завершённой задачи нужного вида или None."""
    with _guard:
        job = _jobs.get(job_id)
        if job is None or job.project_id != project_id or job.kind != kind:
            return None
        return job.result if job.status == "complete" else None


def list_jobs(project_id: str) -> list[dict[str, Any]]:
    """Задачи проекта, новые первыми, без тяжёлых результатов."""
    with _guard:
        jobs = [job for job in _jobs.values() if job.project_id == project_id]
        return [
            job.payload(with_result=False)
            for job in sorted(jobs, key=lambda item: item.created_at, reverse=True)
        ]


def _submit(job: AiJob) -> None:
    _executor.submit(_run, job.id)


def _run(job_id: str) -> None:
    with _guard:
        job = _jobs[job_id]
        job.status = "running"
        job.stage = "Начинаем"
        job.attempts += 1
        function = job.function

    def progress(completed: int, total: int, stage: str) -> None:
        with _guard:
            current = _jobs[job_id]
            current.completed = completed
            current.total = max(1, total)
            current.stage = stage

    try:
        result = function(progress)
    except JobFailure as exc:
        _finish(job_id, error=str(exc), code=exc.code)
    except Exception:
        logger.exception("AI job failed", extra={"job_id": job_id})
        _finish(
            job_id,
            error="Задача не выполнилась из-за внутренней ошибки. Повторите попытку.",
            code="AI_JOB_FAILED",
        )
    else:
        _finish(job_id, result=result)


def _finish(
    job_id: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    code: str | None = None,
) -> None:
    with _guard:
        job = _jobs[job_id]
        job.finished_at = datetime.now(UTC).isoformat()
        if error is not None:
            job.status = "failed"
            job.stage = "Ошибка"
            job.error = error
            job.error_code = code
        else:
            job.status = "complete"
            job.completed = job.total
            job.stage = "Готово"
            job.result = result


def _forget_old(project_id: str) -> None:
    finished = sorted(
        (
            job
            for job in _jobs.values()
            if job.project_id == project_id and job.status in {"complete", "failed"}
        ),
        key=lambda item: item.created_at,
    )
    for job in finished[: max(0, len(finished) - _KEEP_FINISHED)]:
        _jobs.pop(job.id, None)
