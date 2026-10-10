"""Бенчмарк импорта и сборки отчёта (P6, architecture.md §4.2).

    python scripts/benchmark.py [--out результат.json] [--only typical,rows10k,wide]

Три набора:

- `typical` — обычная анкета: 1 500 строк, 80 вопросов;
- `rows10k` — 10 000 строк с тем же составом;
- `wide` — стресс: 3 000 строк, 600 переменных, баннер из пяти разрезов,
  вес, попарные сравнения, Bonferroni и корреляции.

Для каждого — время импорта и сборки по стадиям, которые сообщает сборка,
и общее время. Цель architecture.md: типовой отчёт ≤ 120 секунд от старта
задания до готовых XLSX и statistics.txt. Данные синтетические и
воспроизводимые (seed), в git не попадают — пишутся во временный каталог.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd
import pyreadstat

SETS: dict[str, dict[str, Any]] = {
    "typical": {"rows": 1500, "singles": 50, "scales": 30, "banner": 2, "heavy": False},
    "rows10k": {"rows": 10_000, "singles": 50, "scales": 30, "banner": 2, "heavy": False},
    "wide": {"rows": 3000, "singles": 350, "scales": 250, "banner": 5, "heavy": True},
}


def write_survey(path: Path, rows: int, singles: int, scales: int, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    data: dict[str, Any] = {}
    labels: dict[str, str] = {}
    values: dict[str, dict[float, str]] = {}
    data["GENDER"] = rng.integers(1, 3, rows).astype(float)
    labels["GENDER"] = "Пол"
    values["GENDER"] = {1.0: "Мужчина", 2.0: "Женщина"}
    data["AGEGR"] = rng.integers(1, 5, rows).astype(float)
    labels["AGEGR"] = "Возраст"
    values["AGEGR"] = {1.0: "18–24", 2.0: "25–34", 3.0: "35–54", 4.0: "55+"}
    for region in range(3):
        name = f"SEG{region + 1}"
        data[name] = rng.integers(1, 4, rows).astype(float)
        labels[name] = f"Сегмент {region + 1}"
        values[name] = {1.0: "А", 2.0: "Б", 3.0: "В"}
    for index in range(singles):
        name = f"Q{index + 1}"
        options = 3 + index % 5
        data[name] = rng.integers(1, options + 1, rows).astype(float)
        data[name][rng.random(rows) < 0.05] = np.nan
        labels[name] = f"Вопрос {index + 1}"
        values[name] = {float(code): f"Вариант {code}" for code in range(1, options + 1)}
    for index in range(scales):
        name = f"S{index + 1}"
        data[name] = rng.integers(1, 6, rows).astype(float)
        labels[name] = f"Оценка {index + 1}"
        values[name] = {1.0: "Совсем нет", 3.0: "Средне", 5.0: "Полностью"}
    weight = rng.lognormal(0, 0.3, rows)
    data["WEIGHT"] = weight / weight.mean()
    labels["WEIGHT"] = "Вес"
    pyreadstat.write_sav(
        pd.DataFrame(data),
        str(path),
        column_labels=labels,
        variable_value_labels=values,
    )


def run(name: str, spec: dict[str, Any], scratch: Path) -> dict[str, Any]:
    from sav_analytics.db import sqlite_url
    from sav_analytics.report_cache import prepare_report
    from sav_analytics.repository import ProjectRepository

    source = scratch / f"{name}.sav"
    write_survey(source, spec["rows"], spec["singles"], spec["scales"])
    repository = ProjectRepository(
        scratch / name / "projects", 1024**3, sqlite_url(scratch / name / "bench.db")
    )
    started = time.perf_counter()
    with source.open("rb") as stream:
        project = repository.create(name, source.name, stream)
    imported = time.perf_counter() - started
    project_id = UUID(project["id"])

    cuts = ["GENDER", "AGEGR", "SEG1", "SEG2", "SEG3"][: spec["banner"]]
    banner = repository.create_banner(
        project_id,
        {
            "name": "Разрезы",
            "blocks": [{"sources": [{"kind": "question", "ref": cut}]} for cut in cuts],
        },
    )
    current = repository.get(project_id)["configuration"]["report_settings"]
    settings: dict[str, Any] = {
        **current,
        "compare_to_total": True,
        "weight_variable": "WEIGHT",
    }
    if spec["heavy"]:
        settings.update(compare_pairwise=True, bonferroni=True, correlations=True)
    project = repository.update_report_settings(project_id, settings)
    del banner

    stages: list[tuple[str, float]] = []
    clock = time.perf_counter()

    def progress(_completed: int, _total: int, stage: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        if not stages or stages[-1][0] != stage:
            stages.append((stage, 0.0))
        label, spent = stages[-1]
        stages[-1] = (label, spent + now - clock)
        clock = now

    started = time.perf_counter()
    prepared = prepare_report(repository, project_id, project, progress_callback=progress)
    report_seconds = time.perf_counter() - started
    by_stage: dict[str, float] = {}
    for label, spent in stages:
        # «topline_main: Q12» → «topline_main»: время листа, а не вопроса.
        key = label.split("«")[0].split(":")[0].strip() or label
        by_stage[key] = round(by_stage.get(key, 0.0) + spent, 3)
    return {
        "rows": spec["rows"],
        "variables": spec["singles"] + spec["scales"] + 6,
        "banner_cuts": spec["banner"],
        "import_seconds": round(imported, 3),
        "report_seconds": round(report_seconds, 3),
        "xlsx_bytes": prepared.topline_path.stat().st_size,
        "stages": dict(sorted(by_stage.items(), key=lambda item: -item[1])[:8]),
        "within_target": report_seconds <= 120,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--only", default=",".join(SETS))
    args = parser.parse_args()
    results: dict[str, Any] = {
        "machine": {"python": platform.python_version(), "platform": platform.platform()},
        "sets": {},
    }
    with tempfile.TemporaryDirectory(prefix="sav-bench-") as scratch:
        for name in args.only.split(","):
            print(f"… {name}", file=sys.stderr)
            results["sets"][name] = run(name, SETS[name], Path(scratch))
    text = json.dumps(results, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if all(item["within_target"] for item in results["sets"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
