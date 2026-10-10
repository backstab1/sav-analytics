"""Очередь заданий в базе метаданных (P5).

Задание — строка `jobs`. Жизненный цикл:

    queued ──claim──▶ running ──▶ complete
       ▲                 │  ├───▶ failed      (попытки кончились, timeout)
       └──retry/lease────┘  └───▶ cancelled   (отменено человеком)

- **Идемпотентность.** `active_key` уникален среди незавершённых заданий:
  второй `enqueue` с тем же ключом возвращает уже поставленное задание, а
  не ставит расчёт ещё раз. По завершении ключ снимается.
- **Аренда.** Взявший задание worker держит его до `lease_until` и продлевает
  аренду пульсом. Если worker умер, аренда истекает, и `recover` возвращает
  задание в очередь (или проваливает, если попытки кончились).
- **Timeout.** Задание, которое выполняется дольше `timeout_seconds`,
  проваливается с `JOB_TIMEOUT`, даже если пульс идёт.
- **Отмена.** Задание в очереди отменяется сразу; выполняющееся получает
  `cancel_requested`, и исполнитель прекращает его на ближайшем пульсе.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine, Row
from sqlalchemy.exc import IntegrityError

from ..db.engine import aware, is_sqlite, utcnow
from ..db.schema import jobs

ACTIVE = ("queued", "running")
FINISHED = ("complete", "failed", "cancelled")
# Пауза перед повтором после сбоя: 10 с, 40 с, 90 с …
_RETRY_BASE_SECONDS = 10


class JobNotFoundError(LookupError):
    pass


class JobQueue:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # Постановка --------------------------------------------------------------

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        project_id: str | None = None,
        title: str = "",
        subject: str | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
        timeout_seconds: int = 1800,
        runner: str = "worker",
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """Поставить задание; с ключом — вернуть уже стоящее такое же."""
        if idempotency_key:
            existing = self._active_by_key(idempotency_key)
            if existing is not None:
                return existing
        values = {
            "id": str(uuid4()),
            "kind": kind,
            "project_id": project_id,
            "title": title,
            "subject": subject,
            "status": "queued",
            "active_key": idempotency_key,
            "idempotency_key": idempotency_key,
            "payload": json.dumps(payload, ensure_ascii=False),
            "stage": "В очереди",
            "max_attempts": max(1, max_attempts),
            "timeout_seconds": max(1, timeout_seconds),
            "runner": runner,
            "created_at": utcnow(),
            "created_by": created_by,
        }
        try:
            with self.engine.begin() as connection:
                connection.execute(jobs.insert().values(**values))
        except IntegrityError:
            # Такое же задание поставили между проверкой и вставкой.
            if idempotency_key:
                existing = self._active_by_key(idempotency_key)
                if existing is not None:
                    return existing
            raise
        return self.get(values["id"])

    def record_complete(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        """Отметить только что поставленное задание выполненным без исполнения —
        когда результат уже есть (готовая сборка той же версии)."""
        now = utcnow()
        with self.engine.begin() as connection:
            connection.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .where(jobs.c.status == "queued")
                .values(
                    status="complete",
                    stage="Готово",
                    result=json.dumps(result, ensure_ascii=False),
                    active_key=None,
                    completed=jobs.c.total,
                    started_at=now,
                    finished_at=now,
                )
            )
        return self.get(job_id)

    def _active_by_key(self, key: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(sa.select(jobs).where(jobs.c.active_key == key)).first()
        return None if row is None else job_dict(row)

    # Чтение ------------------------------------------------------------------

    def get(self, job_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(sa.select(jobs).where(jobs.c.id == job_id)).first()
        if row is None:
            raise JobNotFoundError(job_id)
        return job_dict(row)

    def find(self, job_id: str) -> dict[str, Any] | None:
        try:
            return self.get(job_id)
        except JobNotFoundError:
            return None

    def list(
        self,
        *,
        project_id: str | None = None,
        kinds: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = sa.select(jobs).order_by(jobs.c.created_at.desc()).limit(limit)
        if project_id is not None:
            query = query.where(jobs.c.project_id == project_id)
        if kinds:
            query = query.where(jobs.c.kind.in_(kinds))
        with self.engine.connect() as connection:
            return [job_dict(row) for row in connection.execute(query)]

    def counts(self) -> dict[str, int]:
        query = sa.select(jobs.c.status, sa.func.count()).group_by(jobs.c.status)
        with self.engine.connect() as connection:
            return {status: count for status, count in connection.execute(query)}

    # Исполнение --------------------------------------------------------------

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        job_id: str | None = None,
        runner: str = "worker",
        kinds: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        """Забрать одно задание из очереди; None — брать нечего.

        `job_id` — забрать именно это задание (процесс API исполняет
        поставленное им самим, не дожидаясь опроса).
        """
        now = utcnow()
        with self.engine.begin() as connection:
            query = (
                sa.select(jobs.c.id)
                .where(jobs.c.status == "queued")
                .where(jobs.c.runner == runner)
                .where(sa.or_(jobs.c.run_after.is_(None), jobs.c.run_after <= now))
                .order_by(jobs.c.created_at)
                .limit(1)
            )
            if job_id is not None:
                query = query.where(jobs.c.id == job_id)
            if kinds:
                query = query.where(jobs.c.kind.in_(kinds))
            if not is_sqlite(self.engine):
                query = query.with_for_update(skip_locked=True)
            row = connection.execute(query).first()
            if row is None:
                return None
            connection.execute(
                jobs.update()
                .where(jobs.c.id == row.id)
                .values(
                    status="running",
                    worker_id=worker_id,
                    attempts=jobs.c.attempts + 1,
                    started_at=now,
                    heartbeat_at=now,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    stage="Начинаем",
                    error=None,
                    error_code=None,
                )
            )
            claimed = connection.execute(sa.select(jobs).where(jobs.c.id == row.id)).one()
        return job_dict(claimed)

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int,
        completed: int | None = None,
        total: int | None = None,
        stage: str | None = None,
    ) -> str:
        """Продлить аренду и записать прогресс.

        Возвращает, что делать исполнителю: `continue`, `cancel` (отменено
        человеком), `timeout` (вышло время) или `lost` (задание уже не наше —
        аренду забрали после долгой паузы).
        """
        now = utcnow()
        values: dict[str, Any] = {
            "heartbeat_at": now,
            "lease_until": now + timedelta(seconds=lease_seconds),
        }
        if completed is not None:
            values["completed"] = completed
        if total is not None:
            values["total"] = max(1, total)
        if stage is not None:
            values["stage"] = stage
        with self.engine.begin() as connection:
            result = connection.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .where(jobs.c.worker_id == worker_id)
                .where(jobs.c.status == "running")
                .values(**values)
            )
            if result.rowcount != 1:
                return "lost"
            row = connection.execute(
                sa.select(jobs.c.cancel_requested, jobs.c.started_at, jobs.c.timeout_seconds)
                .where(jobs.c.id == job_id)
            ).one()
        if row.cancel_requested:
            return "cancel"
        started = aware(row.started_at)
        if started is not None and now - started > timedelta(seconds=row.timeout_seconds):
            return "timeout"
        return "continue"

    def complete(self, job_id: str, worker_id: str, result: dict[str, Any]) -> bool:
        return self._finish(
            job_id,
            worker_id,
            status="complete",
            stage="Готово",
            result=json.dumps(result, ensure_ascii=False),
            completed=jobs.c.total,
        )

    def fail(
        self,
        job_id: str,
        worker_id: str,
        *,
        error: str,
        code: str,
        retryable: bool = False,
    ) -> str:
        """Провалить попытку; с `retryable` и оставшимися попытками —
        вернуть в очередь с паузой. Возвращает новый статус."""
        with self.engine.begin() as connection:
            row = connection.execute(
                sa.select(jobs.c.attempts, jobs.c.max_attempts)
                .where(jobs.c.id == job_id)
                .where(jobs.c.worker_id == worker_id)
                .where(jobs.c.status == "running")
            ).first()
            if row is None:
                return "lost"
            if retryable and row.attempts < row.max_attempts:
                _requeue(connection, job_id, row.attempts, error, code)
                return "queued"
        self._finish(
            job_id, worker_id, status="failed", stage="Ошибка", error=error, error_code=code
        )
        return "failed"

    def cancelled(self, job_id: str, worker_id: str) -> bool:
        return self._finish(
            job_id,
            worker_id,
            status="cancelled",
            stage="Отменено",
            error="Задание отменено.",
            error_code="JOB_CANCELLED",
        )

    def _finish(self, job_id: str, worker_id: str, **values: Any) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .where(jobs.c.worker_id == worker_id)
                .where(jobs.c.status == "running")
                .values(
                    active_key=None,
                    finished_at=utcnow(),
                    lease_until=None,
                    **values,
                )
            )
        return result.rowcount == 1

    # Управление ---------------------------------------------------------------

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Отменить: из очереди — сразу, выполняющееся — на ближайшем пульсе."""
        now = utcnow()
        with self.engine.begin() as connection:
            connection.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .where(jobs.c.status == "queued")
                .values(
                    status="cancelled",
                    stage="Отменено",
                    error="Задание отменено.",
                    error_code="JOB_CANCELLED",
                    active_key=None,
                    finished_at=now,
                )
            )
            connection.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .where(jobs.c.status == "running")
                .values(cancel_requested=True, stage="Отменяем")
            )
        return self.get(job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        """Повторить провалившееся или отменённое задание с теми же входными
        данными. Если такое же уже стоит в очереди, вернуть его."""
        job = self.get(job_id)
        if job["status"] not in ("failed", "cancelled"):
            return job
        key = job["idempotency_key"]
        if key:
            existing = self._active_by_key(key)
            if existing is not None:
                return existing
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    jobs.update()
                    .where(jobs.c.id == job_id)
                    .where(jobs.c.status.in_(("failed", "cancelled")))
                    .values(
                        status="queued",
                        stage="В очереди",
                        active_key=key,
                        # Счёт попыток продолжается, но ещё одна разрешена.
                        max_attempts=jobs.c.attempts + 1,
                        completed=0,
                        error=None,
                        error_code=None,
                        cancel_requested=False,
                        finished_at=None,
                        run_after=None,
                        worker_id=None,
                    )
                )
        except IntegrityError:
            existing = self._active_by_key(key) if key else None
            if existing is not None:
                return existing
            raise
        return self.get(job_id)

    def recover(self, *, local_worker_ids: tuple[str, ...] = ()) -> dict[str, int]:
        """Подобрать задания, брошенные исполнителями.

        - аренда истекла (worker умер или завис без пульса) — в очередь, если
          попытки остались, иначе провал `WORKER_LOST`;
        - время вышло — провал `JOB_TIMEOUT`;
        - локальное задание процесса, которого больше нет, — провал
          `JOB_INTERRUPTED`: его функция жила в памяти того процесса.
        """
        now = utcnow()
        requeued = failed = timed_out = interrupted = 0
        with self.engine.begin() as connection:
            running = connection.execute(
                sa.select(
                    jobs.c.id,
                    jobs.c.attempts,
                    jobs.c.max_attempts,
                    jobs.c.lease_until,
                    jobs.c.started_at,
                    jobs.c.timeout_seconds,
                    jobs.c.runner,
                    jobs.c.worker_id,
                ).where(jobs.c.status == "running")
            ).all()
            for row in running:
                started = aware(row.started_at)
                lease = aware(row.lease_until)
                expired = lease is None or lease < now
                if started is not None and now - started > timedelta(
                    seconds=row.timeout_seconds
                ):
                    _fail_row(
                        connection, row.id, "JOB_TIMEOUT",
                        "Задание выполнялось дольше допустимого и остановлено.",
                    )
                    timed_out += 1
                elif row.runner == "local" and expired and row.worker_id not in local_worker_ids:
                    _fail_row(
                        connection, row.id, "JOB_INTERRUPTED",
                        "Задание прервано перезапуском сервера. Повторите его.",
                    )
                    interrupted += 1
                elif expired and row.attempts < row.max_attempts:
                    _requeue(
                        connection, row.id, row.attempts,
                        "Исполнитель задания пропал, задание поставлено заново.",
                        "WORKER_LOST",
                    )
                    requeued += 1
                elif expired:
                    _fail_row(
                        connection, row.id, "WORKER_LOST",
                        "Исполнитель задания пропал, попытки исчерпаны.",
                    )
                    failed += 1
            # Локальные задания в очереди без живого процесса тоже не исполнятся.
            orphaned = connection.execute(
                sa.select(jobs.c.id, jobs.c.created_at)
                .where(jobs.c.status == "queued")
                .where(jobs.c.runner == "local")
            ).all()
            for row in orphaned:
                created = aware(row.created_at)
                if created is not None and now - created > timedelta(minutes=10):
                    _fail_row(
                        connection, row.id, "JOB_INTERRUPTED",
                        "Задание прервано перезапуском сервера. Повторите его.",
                    )
                    interrupted += 1
        return {
            "requeued": requeued,
            "failed": failed,
            "timed_out": timed_out,
            "interrupted": interrupted,
        }

    def cleanup(self, older_than: datetime) -> int:
        """Удалить завершённые задания старше срока (TTL)."""
        with self.engine.begin() as connection:
            result = connection.execute(
                jobs.delete()
                .where(jobs.c.status.in_(FINISHED))
                .where(jobs.c.finished_at < older_than)
            )
        return result.rowcount or 0

    def delete_for_project(self, project_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(jobs.delete().where(jobs.c.project_id == project_id))


def _requeue(connection: Connection, job_id: str, attempts: int, error: str, code: str) -> None:
    connection.execute(
        jobs.update()
        .where(jobs.c.id == job_id)
        .values(
            status="queued",
            stage="Ждёт повтора",
            worker_id=None,
            lease_until=None,
            error=error,
            error_code=code,
            run_after=utcnow() + timedelta(seconds=_RETRY_BASE_SECONDS * attempts * attempts),
        )
    )


def _fail_row(connection: Connection, job_id: str, code: str, error: str) -> None:
    connection.execute(
        jobs.update()
        .where(jobs.c.id == job_id)
        .values(
            status="failed",
            stage="Ошибка",
            error=error,
            error_code=code,
            active_key=None,
            lease_until=None,
            finished_at=utcnow(),
        )
    )


def job_dict(row: Row[Any]) -> dict[str, Any]:
    data = dict(row._mapping)
    data["payload"] = json.loads(data["payload"]) if data["payload"] else {}
    data["result"] = json.loads(data["result"]) if data["result"] else None
    for key in ("lease_until", "run_after", "created_at", "started_at", "heartbeat_at",
                "finished_at"):
        value = aware(data[key])
        data[key] = value.isoformat() if value is not None else None
    data["cancel_requested"] = bool(data["cancel_requested"])
    return data
