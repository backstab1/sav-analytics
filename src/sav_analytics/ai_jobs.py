"""Фоновые задачи ИИ: разбор анкеты, кодирование, автоотчёт, сопоставление волны.

Статус, прогресс, результат и ошибка задачи живут в общей очереди в базе
(`jobs/queue.py`, исполнитель `local`): их видит любой экземпляр API, и
после перезапуска колокольчик показывает, чем задача кончилась. Функция
задачи держит модель и аргументы в памяти процесса, поэтому исполняет её
тот процесс API, что поставил: задача, оборванная перезапуском, помечается
прерванной (`JOB_INTERRUPTED`) и запускается заново с экрана, а повтор
упавшей — пока процесс жив — идёт с теми же входными данными.

Функция задачи получает `progress(completed, total, stage)` и возвращает
словарь результата. `JobFailure` — ожидаемая ошибка с текстом для человека;
любое другое исключение пишется в журнал и показывается общим текстом.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .actor import current_actor_id
from .jobs import dispatch
from .jobs.runtime import JobContext
from .jobs.runtime import JobFailure as _JobFailure

Progress = Callable[[int, int, str], None]
JobFunction = Callable[[Progress], dict[str, Any]]

PREFIX = "ai."
# Сколько последних задач проекта показывать в колокольчике.
_KEEP_FINISHED = 20
FAILURE = "Задача не выполнилась из-за внутренней ошибки. Повторите попытку."

_guard = threading.Lock()
_functions: dict[str, JobFunction] = {}


class JobFailure(_JobFailure):
    """Ожидаемая ошибка задачи: текст показывается человеку как есть."""

    def __init__(self, message: str, code: str = "AI_JOB_FAILED") -> None:
        super().__init__(message, code)


class RetryUnavailableError(LookupError):
    """Функция задачи осталась в памяти прежнего процесса."""


def start_job(
    repository: Any,
    project_id: str,
    kind: str,
    title: str,
    function: JobFunction,
    subject: str | None = None,
) -> dict[str, Any]:
    queue = dispatch.queue_for(repository)
    job = queue.enqueue(
        PREFIX + kind,
        {},
        project_id=project_id,
        title=title,
        subject=subject,
        max_attempts=1,
        timeout_seconds=dispatch._settings().report_timeout_seconds,
        runner="local",
        created_by=current_actor_id(),
    )
    with _guard:
        _functions[job["id"]] = function
    dispatch.submit_local(repository, job, _runner(job["id"]))
    return payload(job)


def _runner(job_id: str) -> Callable[[JobContext], dict[str, Any]]:
    def run(context: JobContext) -> dict[str, Any]:
        with _guard:
            function = _functions[job_id]
        return function(context.progress)

    return run


def retry_job(repository: Any, project_id: str, job_id: str) -> dict[str, Any] | None:
    """Повторить упавшую задачу с теми же входными данными."""
    queue = dispatch.queue_for(repository)
    job = _own(queue, project_id, job_id)
    if job is None:
        return None
    if job["status"] not in ("failed", "cancelled"):
        return payload(job)
    with _guard:
        known = job_id in _functions
    if not known:
        raise RetryUnavailableError(job_id)
    job = queue.retry(job_id)
    dispatch.submit_local(repository, job, _runner(job_id))
    return payload(job)


def get_job(repository: Any, project_id: str, job_id: str) -> dict[str, Any] | None:
    job = _own(dispatch.queue_for(repository), project_id, job_id)
    return None if job is None else payload(job)


def job_result(repository: Any, project_id: str, job_id: str, kind: str) -> dict[str, Any] | None:
    """Результат завершённой задачи нужного вида или None."""
    job = _own(dispatch.queue_for(repository), project_id, job_id)
    if job is None or job["kind"] != PREFIX + kind or job["status"] != "complete":
        return None
    return job["result"]


def list_jobs(repository: Any, project_id: str) -> list[dict[str, Any]]:
    """Задачи проекта, новые первыми, без тяжёлых результатов."""
    jobs = dispatch.queue_for(repository).list(project_id=project_id, limit=200)
    jobs = [job for job in jobs if job["kind"].startswith(PREFIX)]
    active = [job for job in jobs if job["status"] in ("queued", "running")]
    finished = [job for job in jobs if job["status"] not in ("queued", "running")]
    return [payload(job, with_result=False) for job in active + finished[:_KEEP_FINISHED]]


def _own(queue: Any, project_id: str, job_id: str) -> dict[str, Any] | None:
    job = queue.find(job_id)
    if job is None or job["project_id"] != project_id or not job["kind"].startswith(PREFIX):
        return None
    return job


def payload(job: dict[str, Any], *, with_result: bool = True) -> dict[str, Any]:
    error = job["error"]
    # Непредвиденная ошибка показывается общим текстом, без подробностей.
    if job["status"] == "failed" and job["error_code"] == "JOB_FAILED":
        error = FAILURE
    return {
        "job_id": job["id"],
        "project_id": job["project_id"],
        "kind": job["kind"].removeprefix(PREFIX),
        "title": job["title"],
        "subject": job["subject"],
        "status": job["status"],
        "completed": job["completed"],
        "total": job["total"],
        "progress": round(job["completed"] / job["total"] * 100) if job["total"] else 0,
        "stage": job["stage"],
        "error": error,
        "error_code": "AI_JOB_FAILED" if job["error_code"] == "JOB_FAILED" else job["error_code"],
        "created_at": job["created_at"],
        "finished_at": job["finished_at"],
        "attempts": job["attempts"],
        "result": job["result"] if with_result else None,
    }
