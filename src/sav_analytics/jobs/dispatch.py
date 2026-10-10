"""Задания в процессе API.

`SAV_ANALYTICS_JOB_RUNNER`:

- `embedded` (по умолчанию, локальный прототип) — процесс API сам
  исполняет задания очереди в фоновых потоках: поставленное задание
  забирается сразу, а фоновый цикл подбирает оставшиеся после перезапуска;
- `external` (production) — API только ставит задания, исполняет их
  отдельный `sav-analytics-worker`; HTTP-процессы тяжёлых расчётов не ведут.

Локальные задания (ИИ с моделью в памяти) исполняются в процессе API
в обоих режимах: их функцию нельзя передать другому процессу.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from ..db.engine import utcnow
from .queue import JobQueue
from .runtime import JobContext, run_job

logger = logging.getLogger(__name__)

WORKER_ID = f"api:{socket.gethostname()}:{os.getpid()}"

_guard = threading.Lock()
_executor: ThreadPoolExecutor | None = None
# Две задачи ИИ сразу: разбор анкеты не должен ждать кодирования массива.
_local_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="local-job")
_loops: set[str] = set()


def _settings() -> Any:
    from ..api_dependencies import get_settings

    return get_settings()


def embedded() -> bool:
    return _settings().job_runner == "embedded"


def queue_for(repository: Any) -> JobQueue:
    return JobQueue(repository.engine)


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _guard:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=max(1, _settings().worker_concurrency),
                thread_name_prefix="job",
            )
        return _executor


def submit(repository: Any, job: dict[str, Any]) -> None:
    """Исполнить поставленное задание здесь же, если API работает без worker."""
    if job["status"] != "queued" or job["runner"] != "worker" or not embedded():
        return
    ensure_background(repository)
    _pool().submit(_claim_and_run, repository, job["id"])


def submit_local(
    repository: Any, job: dict[str, Any], function: Callable[[JobContext], dict[str, Any]]
) -> None:
    ensure_background(repository)
    _local_executor.submit(_claim_and_run, repository, job["id"], function)


def _claim_and_run(
    repository: Any,
    job_id: str | None,
    function: Callable[[JobContext], dict[str, Any]] | None = None,
) -> None:
    queue = queue_for(repository)
    lease = _settings().job_lease_seconds
    job = queue.claim(
        WORKER_ID,
        lease_seconds=lease,
        job_id=job_id,
        runner="local" if function is not None else "worker",
    )
    if job is None:
        return
    try:
        run_job(queue, repository, job, WORKER_ID, lease_seconds=lease, function=function)
    except Exception:  # noqa: BLE001 — поток пула не должен умирать молча
        logger.exception("Job runner crashed", extra={"job_id": job_id})


def ensure_background(repository: Any) -> None:
    """Фоновый цикл процесса API: подбор брошенных заданий и, во встроенном
    режиме, исполнение очереди. Один на базу в процессе."""
    url = repository.database_url
    with _guard:
        if url in _loops:
            return
        _loops.add(url)
    thread = threading.Thread(
        target=_loop, args=(repository,), name="job-maintenance", daemon=True
    )
    thread.start()


def _loop(repository: Any) -> None:
    stop = threading.Event()
    last_cleanup = utcnow() - timedelta(days=1)
    while not stop.wait(_settings().job_poll_seconds):
        try:
            queue = queue_for(repository)
            queue.recover(local_worker_ids=(WORKER_ID,))
            if embedded():
                # Не больше, чем потоков в пуле: остальное подождёт в очереди.
                pool = _pool()
                if pool._work_queue.qsize() == 0:  # noqa: SLF001
                    pool.submit(_claim_and_run, repository, None)
            if utcnow() - last_cleanup > timedelta(hours=1):
                queue.cleanup(utcnow() - timedelta(days=_settings().job_ttl_days))
                last_cleanup = utcnow()
        except Exception:  # noqa: BLE001
            logger.warning("Job maintenance failed", exc_info=True)
