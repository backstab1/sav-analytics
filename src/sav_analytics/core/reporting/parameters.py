"""Лист «Параметры»: из чего и как собрана книга (роадмап, P1.6).

Книга должна объяснять себя без доступа к живому проекту: какой файл, какая
версия настроек, какой фильтр и вес, что выведено. Строки собираются из тех же
функций, что шапка `statistics.txt`, поэтому лист и аудит не расходятся в
формулировках.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..weight_validation import weight_diagnostics
from .data import ReportData
from .statistics import (
    _comparison_scheme_line,
    _number,
    _output_line,
    _report_filter_line,
    _source_lines,
)

_WAVES = {
    "none": "не сравниваются",
    "previous": "каждая волна с предыдущей",
}


def report_parameters(project: dict[str, Any], data: ReportData) -> list[tuple[str, str]]:
    settings = data.statistical_settings
    configuration = data.configuration
    banner = data.active_banner or {}
    if banner:
        blocks = "; ".join(block.get("label") or "блок" for block in banner.get("blocks", []))
        banner_text = (
            f"{banner.get('name', 'Баннер')} — {blocks}; колонок с Total: {len(data.columns)}"
        )
    else:
        banner_text = "не используется, только Total"
    wave = settings.get("wave_comparison", "none")
    wave_text = _WAVES.get(wave) or f"с контрольной волной {settings.get('wave_control_value')}"
    source = [
        (label, value)
        for label, value in (line.split(": ", 1) for line in _source_lines(project, configuration))
    ]
    return [
        ("Проект", str(project["name"])),
        ("Исходный SAV", str(project.get("original_filename", "source.sav"))),
        *source,
        ("Дата расчёта", datetime.now().astimezone().isoformat(timespec="seconds")),
        (
            "Вопросов в отчёте",
            f"{len(data.questions)}; задавались не всем — {len(data.filter_questions)}",
        ),
        ("Total, N", str(data.columns[0]["base"])),
        ("Баннер", banner_text),
        ("Колонки без респондентов", ", ".join(data.empty_columns) or "нет"),
        ("Общий фильтр", _report_filter_line(project, configuration)),
        ("Вес", settings["weight_label"] or "не используется"),
        ("Диагностика веса", _weight_line(data)),
        ("Уровень доверия", f"{_number(settings['confidence_level'] * 100)}%"),
        (
            "Второй уровень доверия",
            f"{settings['secondary_confidence_level'] * 100:g}%, строчные буквы"
            if settings.get("secondary_confidence_level")
            else "не используется",
        ),
        ("Bonferroni", "включена" if settings["bonferroni"] else "выключена"),
        ("Порог малой базы", f"N < {settings['minimum_base']}"),
        ("Схема сравнения", _comparison_scheme_line(banner).split(": ", 1)[1]),
        ("Сравнение волн", wave_text),
        ("Вывод", _output_line(settings).split(": ", 1)[1]),
        (
            "Свой набор вывода",
            ", ".join(item["code"] for item in data.questions if item.get("output_metrics"))
            or "нет",
        ),
        ("p-value в примечаниях", "включён" if settings["show_p_values"] else "выключен"),
        ("Графики", "лист «Графики»" if settings.get("show_charts") else "не выводятся"),
        (
            "Общие тесты",
            "хи-квадрат и Welch ANOVA, без взвешивания"
            if settings.get("overall_tests")
            else "не выполняются",
        ),
        (
            "Лист Correlations",
            "связи числовых вопросов, без веса"
            if settings.get("correlations")
            else "не выводится",
        ),
        ("Замечания проверки", _findings_line(project, data)),
    ]


def _weight_line(data: ReportData) -> str:
    """Числа веса, по которым видно, насколько он сжимает выборку."""
    weights = data.statistical_settings["weights"]
    if weights is None:
        return "не применяется"
    within = weights[data.columns[0]["mask"]]
    if within.empty:
        return "нет респондентов"
    diagnostics = weight_diagnostics(within)
    return (
        f"N {diagnostics.count}; вес от {_number(diagnostics.minimum)} до "
        f"{_number(diagnostics.maximum)}; эффективная база "
        f"{_number(diagnostics.effective_base)}; design effect "
        f"{_number(diagnostics.design_effect)}; эффективность "
        f"{_number(diagnostics.efficiency_percent)}%; крайних весов "
        f"{diagnostics.extreme_count}"
    )


def _findings_line(project: dict[str, Any], data: ReportData) -> str:
    # Импорт здесь: проверка сама строится на подготовке данных отчёта.
    from ..preflight import preflight_warnings

    findings = preflight_warnings(data, project)
    return " | ".join(finding.message for finding in findings) or "нет"


__all__ = ["report_parameters"]
