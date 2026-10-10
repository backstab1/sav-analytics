"""Метрики для Prometheus (P6): `GET /metrics`.

Часть считается в процессе (запросы HTTP), часть — из базы на момент
опроса (очередь, длительность и сбои заданий), поэтому метрики очереди
одинаковы на любом экземпляре API. Объём тома считается не чаще раза в
пять минут: обход каталога данных дорогой.

Доступ: заголовок `Authorization: Bearer <SAV_ANALYTICS_METRICS_TOKEN>`;
без настроенного токена — только с самой машины (127.0.0.1).
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from .db.engine import aware, utcnow
from .db.schema import jobs, projects

_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
_guard = threading.Lock()
_requests: dict[tuple[str, str], int] = defaultdict(int)
_durations: dict[str, list[float]] = defaultdict(lambda: [0.0] * (len(_BUCKETS) + 2))
_storage: dict[str, Any] = {"at": 0.0, "bytes": 0}


def observe_request(method: str, status: int, seconds: float) -> None:
    family = f"{status // 100}xx"
    with _guard:
        _requests[(method, family)] += 1
        histogram = _durations[method]
        for index, bound in enumerate(_BUCKETS):
            if seconds <= bound:
                histogram[index] += 1
        histogram[len(_BUCKETS)] += 1  # +Inf
        histogram[len(_BUCKETS) + 1] += seconds  # sum


def _storage_bytes(root: Path) -> int:
    now = time.monotonic()
    if now - _storage["at"] < 300:
        return int(_storage["bytes"])
    total = 0
    for directory, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(directory, name))
            except OSError:
                continue
    _storage.update(at=now, bytes=total)
    return total


def render(engine: sa.engine.Engine, data_dir: Path) -> str:
    lines: list[str] = []

    def metric(name: str, kind: str, help_text: str, samples: list[tuple[str, float]]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            lines.append(f"{name}{labels} {value}")

    with _guard:
        requests = dict(_requests)
        durations = {method: list(values) for method, values in _durations.items()}
    metric(
        "sav_http_requests_total", "counter", "HTTP-запросы по методу и классу ответа.",
        [(f'{{method="{m}",status="{s}"}}', v) for (m, s), v in sorted(requests.items())],
    )
    histogram: list[tuple[str, float]] = []
    for method, values in sorted(durations.items()):
        for index, bound in enumerate(_BUCKETS):
            histogram.append((f'_bucket{{method="{method}",le="{bound}"}}', values[index]))
        histogram.append((f'_bucket{{method="{method}",le="+Inf"}}', values[len(_BUCKETS)]))
        histogram.append((f'_count{{method="{method}"}}', values[len(_BUCKETS)]))
        histogram.append((f'_sum{{method="{method}"}}', round(values[len(_BUCKETS) + 1], 6)))
    lines.append("# HELP sav_http_request_duration_seconds Время ответа HTTP.")
    lines.append("# TYPE sav_http_request_duration_seconds histogram")
    lines.extend(
        f"sav_http_request_duration_seconds{suffix} {value}" for suffix, value in histogram
    )

    now = utcnow()
    day_ago = now - timedelta(days=1)
    with engine.connect() as connection:
        by_status = connection.execute(
            sa.select(jobs.c.kind, jobs.c.status, sa.func.count()).group_by(
                jobs.c.kind, jobs.c.status
            )
        ).all()
        oldest = connection.execute(
            sa.select(sa.func.min(jobs.c.created_at)).where(jobs.c.status == "queued")
        ).scalar()
        finished = connection.execute(
            sa.select(jobs.c.kind, jobs.c.status, jobs.c.started_at, jobs.c.finished_at,
                      jobs.c.error_code)
            .where(jobs.c.finished_at >= day_ago)
        ).all()
        project_count = connection.execute(
            sa.select(sa.func.count()).select_from(projects).where(projects.c.trashed_at.is_(None))
        ).scalar()
    metric(
        "sav_jobs", "gauge", "Задания по виду и статусу.",
        [(f'{{kind="{k}",status="{s}"}}', n) for k, s, n in by_status],
    )
    oldest_at = aware(oldest)
    metric(
        "sav_queue_oldest_seconds", "gauge", "Сколько ждёт самое старое задание в очереди.",
        [("", round((now - oldest_at).total_seconds(), 1) if oldest_at else 0.0)],
    )
    durations_by_kind: dict[str, list[float]] = defaultdict(list)
    failures: dict[tuple[str, str], int] = defaultdict(int)
    for row in finished:
        started, ended = aware(row.started_at), aware(row.finished_at)
        if row.status == "complete" and started and ended:
            durations_by_kind[row.kind].append((ended - started).total_seconds())
        if row.status == "failed":
            failures[(row.kind, row.error_code or "")] += 1
    metric(
        "sav_job_duration_seconds_avg_24h", "gauge",
        "Средняя длительность выполненных заданий за сутки.",
        [(f'{{kind="{k}"}}', round(sum(v) / len(v), 3)) for k, v in durations_by_kind.items()],
    )
    metric(
        "sav_job_duration_seconds_max_24h", "gauge",
        "Самое долгое выполненное задание за сутки.",
        [(f'{{kind="{k}"}}', round(max(v), 3)) for k, v in durations_by_kind.items()],
    )
    metric(
        "sav_job_failures_24h", "gauge", "Провалившиеся задания за сутки по коду ошибки.",
        [(f'{{kind="{k}",code="{c}"}}', n) for (k, c), n in failures.items()],
    )
    metric("sav_projects", "gauge", "Проекты в библиотеке.", [("", float(project_count or 0))])
    metric(
        "sav_storage_bytes", "gauge", "Объём каталога данных.",
        [("", float(_storage_bytes(data_dir)))],
    )
    lines.append("")
    return "\n".join(lines)
