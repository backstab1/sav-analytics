from __future__ import annotations

from dataclasses import dataclass

from ..statistics import StatisticalTestResult


class ReportError(ValueError):
    """Сборка невозможна.

    `code` заполняется там, где у отказа есть собственная причина, отличимая от
    общей неготовности конфигурации: preflight отдаёт его как код находки,
    иначе непригодный вес и сломанный баннер выглядят для клиента одинаково.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


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
