"""Исполнение задания: обработчик по виду, пульс, отмена и timeout.

Обработчик получает `JobContext` и возвращает словарь результата.
`JobFailure` — ожидаемая ошибка с текстом для человека; любое другое
исключение пишется в журнал и показывается общим текстом обработчика.

Пока обработчик работает, отдельный поток продлевает аренду, даже если
сам обработчик долго не сообщает прогресс (запись большой книги). Отмену и
timeout поток только замечает: остановить Python-поток снаружи нельзя,
поэтому обработчик прерывается на ближайшем `progress`. Worker с
`isolation=process` вдобавок убивает процесс задания (`worker.py`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..actor import Actor, bind_actor, reset_actor
from .queue import JobQueue

logger = logging.getLogger(__name__)

Progress = Callable[[int, int, str], None]


class JobFailure(RuntimeError):
    """Ожидаемая ошибка задания: текст показывается человеку как есть."""

    def __init__(self, message: str, code: str = "JOB_FAILED", *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class JobStopped(BaseException):
    """Задание остановлено снаружи: отмена, timeout или потеря аренды.

    `BaseException`, чтобы `except Exception` внутри расчёта его не съел.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Handler:
    function: Callable[[JobContext], dict[str, Any]]
    # Текст для человека, если обработчик упал непредвиденно.
    failure_message: str = "Задание не выполнилось из-за внутренней ошибки. Повторите попытку."
    failure_code: str = "JOB_FAILED"


_handlers: dict[str, Handler] = {}


def register(
    kind: str,
    *,
    failure_message: str | None = None,
    failure_code: str | None = None,
) -> Callable[[Callable[[JobContext], dict[str, Any]]], Callable[[JobContext], dict[str, Any]]]:
    def decorate(
        function: Callable[[JobContext], dict[str, Any]],
    ) -> Callable[[JobContext], dict[str, Any]]:
        handler = Handler(function)
        if failure_message:
            handler.failure_message = failure_message
        if failure_code:
            handler.failure_code = failure_code
        _handlers[kind] = handler
        return function

    return decorate


def handler_for(kind: str) -> Handler | None:
    if kind not in _handlers:
        # Обработчики регистрируются при импорте своих модулей.
        from . import handlers  # noqa: F401
    return _handlers.get(kind)


@dataclass
class JobContext:
    job: dict[str, Any]
    repository: Any
    queue: JobQueue
    worker_id: str
    lease_seconds: int
    _stop: str | None = None
    _last_write: float = 0.0
    _last_stage: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def payload(self) -> dict[str, Any]:
        return self.job["payload"]

    @property
    def project_id(self) -> str | None:
        return self.job["project_id"]

    def progress(self, completed: int, total: int, stage: str) -> None:
        """Сообщить прогресс; бросает `JobStopped`, если задание остановлено."""
        self.check()
        now = time.monotonic()
        with self._lock:
            # Пишем при смене стадии и не чаще пяти раз в секунду.
            if stage == self._last_stage and now - self._last_write < 0.2:
                return
            self._last_stage = stage
            self._last_write = now
        verdict = self.queue.heartbeat(
            self.job["id"],
            self.worker_id,
            lease_seconds=self.lease_seconds,
            completed=completed,
            total=total,
            stage=stage,
        )
        self._note(verdict)
        self.check()

    def check(self) -> None:
        if self._stop is not None:
            raise JobStopped(self._stop)

    def _note(self, verdict: str) -> None:
        if verdict != "continue":
            self._stop = verdict


def run_job(
    queue: JobQueue,
    repository: Any,
    job: dict[str, Any],
    worker_id: str,
    *,
    lease_seconds: int = 60,
    function: Callable[[JobContext], dict[str, Any]] | None = None,
) -> str:
    """Выполнить уже взятое (`claim`) задание и записать итог. Возвращает статус.

    `function` — для локальных заданий процесса API, функция которых живёт
    в памяти; иначе обработчик берётся по виду задания.
    """
    handler = Handler(function) if function is not None else handler_for(job["kind"])
    if handler is None:
        queue.fail(
            job["id"], worker_id,
            error=f"Неизвестный вид задания: {job['kind']}.", code="JOB_UNKNOWN_KIND",
        )
        return "failed"
    context = JobContext(job, repository, queue, worker_id, lease_seconds)
    beating = threading.Event()

    def heartbeat() -> None:
        interval = max(1.0, lease_seconds / 3)
        while not beating.wait(interval):
            try:
                context._note(
                    queue.heartbeat(job["id"], worker_id, lease_seconds=lease_seconds)
                )
            except Exception:  # noqa: BLE001 — пульс не должен ронять задание
                logger.warning("Job heartbeat failed", exc_info=True,
                               extra={"job_id": job["id"]})

    pulse = threading.Thread(target=heartbeat, name=f"job-heartbeat-{job['id'][:8]}",
                             daemon=True)
    pulse.start()
    actor = Actor(job["created_by"], "", "user") if job.get("created_by") else None
    token = bind_actor(actor)
    try:
        result = handler.function(context)
        context.check()
    except JobStopped as stop:
        return _stopped(queue, job, worker_id, stop.reason)
    except JobFailure as exc:
        return queue.fail(
            job["id"], worker_id, error=str(exc), code=exc.code, retryable=exc.retryable
        )
    except Exception:
        logger.exception(
            "Job failed",
            extra={"job_id": job["id"], "job_kind": job["kind"],
                   "project_id": job.get("project_id")},
        )
        return queue.fail(
            job["id"], worker_id, error=handler.failure_message, code=handler.failure_code
        )
    finally:
        beating.set()
        reset_actor(token)
    queue.complete(job["id"], worker_id, result or {})
    return "complete"


def _stopped(queue: JobQueue, job: dict[str, Any], worker_id: str, reason: str) -> str:
    if reason == "cancel":
        queue.cancelled(job["id"], worker_id)
        return "cancelled"
    if reason == "timeout":
        queue.fail(
            job["id"], worker_id,
            error="Задание выполнялось дольше допустимого и остановлено.",
            code="JOB_TIMEOUT",
        )
        return "failed"
    # Аренду забрали: итог запишет тот, кто взял задание после нас.
    return "lost"
