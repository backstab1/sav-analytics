from __future__ import annotations

from dataclasses import dataclass

from ..statistics import StatisticalTestResult


class ReportError(ValueError):
    pass


@dataclass(frozen=True)
class ToplineArtifacts:
    xlsx: bytes
    statistics_txt: str


@dataclass(frozen=True)
class StatisticalAuditEntry:
    sheet: str
    question_code: str
    question_label: str
    row_label: str
    comparison: str
    group_a: str
    group_b: str
    result: StatisticalTestResult | None
    reason: str | None = None
