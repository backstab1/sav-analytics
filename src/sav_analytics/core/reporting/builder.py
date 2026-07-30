from __future__ import annotations

from collections.abc import Callable
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, TextIO

import xlsxwriter

from .data import prepare_report_data
from .excel_layout import _write_contents, _write_topline
from .models import StatisticalAuditEntry, ToplineArtifacts
from .statistics import _StatisticsAuditWriter
from .styles import _formats


def build_topline_xlsx(path: str | Path, project: dict[str, Any]) -> bytes:
    return build_topline_artifacts(path, project).xlsx

def build_statistics_txt(path: str | Path, project: dict[str, Any]) -> str:
    return build_topline_artifacts(path, project).statistics_txt

def build_topline_artifacts(
    path: str | Path,
    project: dict[str, Any],
    *,
    statistics_stream: TextIO | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> ToplineArtifacts:
    data = prepare_report_data(path, project)
    total_steps = data.total_steps
    completed_steps = 0

    def advance(stage: str) -> None:
        nonlocal completed_steps
        completed_steps += 1
        if progress_callback is not None:
            progress_callback(completed_steps, total_steps, stage)

    if progress_callback is not None:
        progress_callback(0, total_steps, "Чтение SAV и подготовка баннера")

    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties(
        {
            "title": f"Топлайн — {project['name']}",
            "subject": "Автоматически сформированный аналитический отчёт",
            "author": "sav-analytics",
        }
    )
    formats = _formats(workbook)
    audit_entries: list[StatisticalAuditEntry] = []
    statistics_buffer = StringIO() if statistics_stream is None else statistics_stream
    audit_writer = _StatisticsAuditWriter(
        statistics_buffer,
        project,
        data.active_banner,
        data.configuration,
        data.statistical_settings,
    )
    main = workbook.add_worksheet("topline_main")
    filtered = workbook.add_worksheet("topline_filter")
    contents = workbook.add_worksheet("Содержание")
    main_rows = _write_topline(
        main,
        data.frame,
        project,
        data.questions,
        data.variables,
        data.filters,
        data.columns,
        formats,
        data.statistical_settings,
        audit_entries,
        "topline_main",
        valid_denominator=False,
        audit_writer=audit_writer,
        advance=advance,
    )
    filter_rows = _write_topline(
        filtered,
        data.frame,
        project,
        data.filter_questions,
        data.variables,
        data.filters,
        data.columns,
        formats,
        data.statistical_settings,
        audit_entries,
        "topline_filter",
        valid_denominator=True,
        audit_writer=audit_writer,
        advance=advance,
    )
    _write_contents(contents, project, data.questions, main_rows, filter_rows, formats)
    workbook.close()
    advance("Запись Excel")
    audit_writer.finish()
    advance("Запись статистики")
    statistics_txt = statistics_buffer.getvalue() if isinstance(statistics_buffer, StringIO) else ""
    return ToplineArtifacts(xlsx=output.getvalue(), statistics_txt=statistics_txt)

