"""Отдельный исполнитель заданий: `sav-analytics-worker` (P5).

Берёт задания из очереди в базе, по `SAV_ANALYTICS_WORKER_CONCURRENCY`
одновременно. С `SAV_ANALYTICS_WORKER_ISOLATION=process` каждое задание идёт
в своём процессе: если вышло время или задание отменили, процесс
убивается, а предел памяти (`SAV_ANALYTICS_JOB_MEMORY_LIMIT_MB`, Linux)
не даёт одному широкому отчёту вытеснить остальные. Процесс задания,
упавший сам (например, убитый ядром за память), — повод повторить задание,
пока попытки не кончились.

SIGTERM: новые задания не берутся, текущие доделываются. Если worker
убьют, не дождавшись, аренда его заданий истечёт, и их подберёт другой.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import shutil
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Any

from .jobs.queue import JobQueue
from .jobs.runtime import run_job
from .settings import Settings

logger = logging.getLogger("sav_analytics.worker")


def _repository(settings: Settings):  # type: ignore[no-untyped-def]
    from .repository import ProjectRepository

    return ProjectRepository(
        settings.projects_dir,
        settings.max_upload_bytes,
        settings.resolved_database_url,
        migrate=settings.auto_migrate,
    )


def _child(job_id: str, worker_id: str) -> None:
    """Процесс одного задания: своё соединение с базой, тот же обработчик."""
    settings = Settings()
    _limit_memory(settings.job_memory_limit_mb)
    from .logging_setup import configure_logging

    configure_logging(settings)
    repository = _repository(settings)
    queue = JobQueue(repository.engine)
    job = queue.get(job_id)
    run_job(queue, repository, job, worker_id, lease_seconds=settings.job_lease_seconds)


def _limit_memory(megabytes: int) -> None:
    if megabytes <= 0:
        return
    try:
        import resource  # только POSIX
    except ImportError:
        return
    limit = megabytes * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))  # type: ignore[attr-defined]


@dataclass
class Running:
    job: dict[str, Any]
    started: float
    process: BaseProcess | None = None
    thread: threading.Thread | None = None

    def alive(self) -> bool:
        if self.process is not None:
            return self.process.is_alive()
        return self.thread is not None and self.thread.is_alive()


class Worker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = _repository(settings)
        self.queue = JobQueue(self.repository.engine)
        self.id = f"worker:{socket.gethostname()}:{os.getpid()}"
        self.running: dict[str, Running] = {}
        self.stopping = threading.Event()
        self._context = multiprocessing.get_context("spawn")

    def run(self) -> None:
        logger.info("Worker started", extra={"worker_id": self.id})
        while not (self.stopping.is_set() and not self.running):
            self.tick()
            time.sleep(self.settings.job_poll_seconds)
        logger.info("Worker stopped", extra={"worker_id": self.id})

    def tick(self) -> None:
        self._reap()
        self.queue.recover()
        if self.stopping.is_set():
            return
        while len(self.running) < max(1, self.settings.worker_concurrency):
            if not self._disk_ok():
                break
            job = self.queue.claim(self.id, lease_seconds=self.settings.job_lease_seconds)
            if job is None:
                break
            self._start(job)

    def _disk_ok(self) -> bool:
        free = shutil.disk_usage(self.settings.data_dir).free // (1024 * 1024)
        if free < self.settings.min_free_disk_mb:
            logger.warning("Low disk space, not taking jobs", extra={"free_mb": free})
            return False
        return True

    def _start(self, job: dict[str, Any]) -> None:
        logger.info("Job started", extra={"job_id": job["id"], "job_kind": job["kind"]})
        running = Running(job, time.monotonic())
        if self.settings.worker_isolation == "process":
            process = self._context.Process(
                target=_child, args=(job["id"], self.id), name=f"job-{job['id'][:8]}"
            )
            process.start()
            running.process = process
        else:
            thread = threading.Thread(
                target=run_job,
                args=(self.queue, self.repository, job, self.id),
                kwargs={"lease_seconds": self.settings.job_lease_seconds},
                name=f"job-{job['id'][:8]}",
                daemon=True,
            )
            thread.start()
            running.thread = thread
        self.running[job["id"]] = running

    def _reap(self) -> None:
        for job_id, running in list(self.running.items()):
            state = self.queue.find(job_id)
            if running.alive():
                if running.process is None or state is None:
                    continue
                overdue = time.monotonic() - running.started > state["timeout_seconds"]
                if state["cancel_requested"] or overdue:
                    # Процесс не отвечает на остановку сам — убиваем.
                    running.process.terminate()
                    running.process.join(10)
                    if running.process.is_alive():
                        running.process.kill()
                    if state["status"] == "running":
                        if state["cancel_requested"]:
                            self.queue.cancelled(job_id, self.id)
                        else:
                            self.queue.fail(
                                job_id, self.id,
                                error="Задание выполнялось дольше допустимого и остановлено.",
                                code="JOB_TIMEOUT",
                            )
                    del self.running[job_id]
                continue
            del self.running[job_id]
            if state is not None and state["status"] == "running" and state["worker_id"] == self.id:
                # Процесс задания умер, не записав итог.
                code = running.process.exitcode if running.process else None
                self.queue.fail(
                    job_id, self.id,
                    error="Процесс задания завершился аварийно.",
                    code="WORKER_CRASHED",
                    retryable=True,
                )
                logger.error("Job process crashed", extra={"job_id": job_id, "exitcode": code})
            else:
                logger.info("Job finished", extra={"job_id": job_id,
                                                   "status": state and state["status"]})

    def stop(self, *_args: Any) -> None:
        self.stopping.set()


def main() -> int:
    settings = Settings()
    from .logging_setup import configure_logging

    configure_logging(settings)
    worker = Worker(settings)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
