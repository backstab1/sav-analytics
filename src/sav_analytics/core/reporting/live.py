"""Живая таблица: те же строки, что пишет книга, без файла Excel.

Экран «Таблицы» не получает своих формул. Таблица собирается функцией
:func:`write_topline` — той же, что пишет лист книги, — только вместо листа
xlsxwriter ей передаётся лист, который запоминает записанное: значение ячейки,
её формат и примечание. Цвет значимости, стрелка волны, серая малая база и
число знаков читаются из того же формата, что уходит в Excel. Поэтому число
на экране и число в книге совпадают по построению, а не по сверке, и
расходиться им негде (роадмап, PQ.3).

Раскладка экрана — вопросы строк, разрез колонок, фильтр — подставляется в
копию проекта. Сохранённая конфигурация не меняется.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..banner import BannerError, banner_columns
from ..filtering import FilterError, filter_columns
from ..multiple_response import is_multiple, response_options
from ..report_settings import resolved_report_settings
from ..waves import wave_view
from .builder import build_topline_artifacts, counts_report_data
from .data import prepare_report_data
from .excel_layout import banner_blocks, write_topline
from .models import ReportError, StatisticalAuditEntry
from .recording import (
    RecordingSheet,
    RecordingWorkbook,
    table_columns,
    table_questions,
)
from .styles import report_formats

#: Типы, которые умеет раскладывать лист книги. Открытый текст и технические
#: переменные в отчёт не входят, и таблица их тоже не строит.
LIVE_QUESTION_TYPES = frozenset(
    {
        "single_choice",
        "scale",
        "numeric",
        "multiple_choice_dichotomy",
        "multiple_choice_categorical",
        "ranking",
        "matrix",
    }
)

def build_live_table(
    path: str | Path,
    project: dict[str, Any],
    *,
    questions: list[str],
    banner_id: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
    filter_id: str | None = None,
    sheet: str = "main",
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Посчитать таблицу для экрана.

    `sheet="main"` — доли от полной базы, как `topline_main`; `"filter"` —
    от валидной базы вопроса, как `topline_filter`; `"counts"` — числа
    ответивших, как лист «Счётчики», без долей и тестов. `overrides` — разовые
    настройки вопроса на этом экране (NET-группы и размер Top/Bottom): они
    не сохраняются в проект, но считаются тем же кодом, что книга.
    """
    live = _live_project(project, questions, banner_id, blocks, filter_id, overrides=overrides)
    data = prepare_report_data(path, live, columns=_needed_columns(live))
    counts = sheet == "counts"
    if counts:
        data = counts_report_data(data)
    recording = RecordingSheet()
    valid = sheet == "filter"
    chosen = data.filter_questions if valid else data.questions
    entries: list[StatisticalAuditEntry] = []
    positions = write_topline(
        recording,
        data,
        live,
        chosen,
        report_formats(RecordingWorkbook(), data.statistical_settings),
        entries,
        "Счётчики" if counts else "topline_filter" if valid else "topline_main",
        valid_denominator=valid,
    )
    settings = resolved_report_settings(data.configuration, data.active_banner)
    header_rows = 6 if data.statistical_settings["weights"] is not None else 5
    return {
        "sheet": sheet,
        "settings": {
            "confidence_level": settings["confidence_level"],
            "bonferroni": settings["bonferroni"],
            "minimum_base": settings["minimum_base"],
            # Числа с колонками не сравниваются: схема отчёта к ним не относится.
            "compare_to_total": settings["compare_to_total"] and not counts,
            "compare_target": settings["compare_target"],
            "compare_pairwise": settings["compare_pairwise"] and not counts,
            "secondary_confidence_level": settings.get("secondary_confidence_level"),
            "weight": data.statistical_settings["weight_label"],
        },
        "columns": table_columns(recording, data, header_rows),
        "blocks": [
            {"label": label, "first": start - 1, "last": end - 1}
            for start, end, label in banner_blocks(data.columns)
            if label
        ],
        "empty_columns": data.empty_columns,
        "questions": table_questions(recording, chosen, positions, len(data.columns)),
        "tests": len(entries),
    }


def export_live_table(
    path: str | Path,
    project: dict[str, Any],
    *,
    questions: list[str] | None,
    banner_id: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
    filter_id: str | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
    counts_sheet: bool = False,
) -> tuple[bytes, str]:
    """Книга по раскладке экрана — тем же сборщиком и в том же стиле.

    Отличие от полного отчёта только в составе: в книгу идут вопросы строк,
    разрез экрана и его фильтр. Настройки вывода, тесты и вес — проектные,
    поэтому выгруженная таблица совпадает с тем же местом полного отчёта.
    Возвращается книга и `statistics.txt` к ней.

    `questions=None` — все вопросы, включённые в отчёт, в порядке структуры:
    полный отчёт с разрезом и фильтром экрана. Тогда состав не сужается до
    типов экрана — книга выводит те же вопросы, что вывела бы полная сборка.
    `counts_sheet` добавляет лист «Счётчики», даже если в книге он выключен:
    экран показывал числа, и выгрузка их не теряет.
    """
    live = _live_project(
        project, questions, banner_id, blocks, filter_id, show_protocol=False, overrides=overrides
    )
    if counts_sheet:
        live["configuration"]["report_settings"]["counts_sheet"] = True
    artifacts = build_topline_artifacts(path, live)
    return artifacts.xlsx, artifacts.statistics_txt


def _needed_columns(live: dict[str, Any]) -> list[str] | None:
    """Столбцы SAV, нужные этой раскладке, или None — если нужны все.

    Читать весь массив на каждый пересчёт незачем: таблице нужны вопросы
    строк, переменные разреза, фильтров и веса. Список собирают сами модули,
    чтобы имена столбцов не приходилось угадывать снаружи.
    """
    configuration = live["configuration"]
    settings = configuration.get("report_settings") or {}
    if settings.get("calculated_weight_id"):
        # Raking считается по целям веса: какие столбцы ему нужны, знает он сам.
        return None
    names: list[str] = []

    def add(name: str) -> None:
        if name not in names:
            names.append(name)

    filters = {item["id"]: item for item in configuration.get("filters", [])}
    rules = [configuration.get("report_filter_id")]
    for question in configuration["questions"]:
        if not question["included_in_report"]:
            continue
        for name in question["source_variables"]:
            add(name)
        rules.append(question.get("base_filter_id"))
    for rule_id in rules:
        definition = filters.get(rule_id) if rule_id else None
        if definition is None:
            continue
        try:
            for name in sorted(filter_columns(definition, live)):
                add(name)
        except FilterError:
            # Правило сломано — пусть об этом скажет расчёт, а не чтение файла.
            return None
    active = next(
        (
            item
            for item in configuration.get("banners", [])
            if str(item.get("id")) == str(configuration.get("report_banner_id"))
        ),
        None,
    )
    if active is not None:
        try:
            for name in sorted(banner_columns(active, live)):
                add(name)
        except BannerError:
            return None
    weight = settings.get("weight_variable")
    if weight:
        add(weight)
    return names


def _live_project(
    project: dict[str, Any],
    questions: list[str] | None,
    banner_id: str | None,
    blocks: list[dict[str, Any]] | None,
    filter_id: str | None,
    *,
    show_protocol: bool = True,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    live = copy.deepcopy(project)
    configuration = live["configuration"]
    if questions is not None:
        _choose_questions(configuration, questions)
    _apply_overrides(live, overrides or {})

    banners = configuration.get("banners", [])
    if blocks:
        temporary = {"id": str(uuid4()), "name": "Разрез таблицы", "blocks": blocks}
        configuration["banners"] = [*banners, temporary]
        configuration["report_banner_id"] = temporary["id"]
    elif banner_id:
        if not any(str(item.get("id")) == str(banner_id) for item in banners):
            raise ReportError("Баннер не найден.")
        configuration["report_banner_id"] = str(banner_id)
    else:
        configuration["report_banner_id"] = None
    configuration["report_filter_id"] = str(filter_id) if filter_id else None

    settings = dict(configuration.get("report_settings") or {})
    if show_protocol:
        # Экран показывает протокол теста по щелчку, поэтому примечание
        # собирается полным. На числа и решение теста это не влияет.
        settings["show_p_values"] = True
    if (
        settings.get("wave_comparison", "none") != "none"
        and not _has_wave_column(configuration)
        and wave_view(live)["mode"] == "all"
    ):
        settings["wave_comparison"] = "none"
        settings["wave_control_value"] = None
    configuration["report_settings"] = settings
    return live


def _apply_overrides(live: dict[str, Any], overrides: dict[str, dict[str, Any]]) -> None:
    """Разовые NET-группы и размер Top/Bottom экрана — поверх настроек вопроса.

    Правится копия проекта, поэтому вопрос в проекте не меняется: экран
    показывает «что было бы», не сохраняя этого. Значения NET экран знает
    подписями строк таблицы, поэтому подпись здесь переводится в код ответа.
    """
    if not overrides:
        return
    configuration = live["configuration"]
    by_code = {item["code"]: item for item in configuration["questions"]}
    for code, override in overrides.items():
        question = by_code.get(code)
        if question is None:
            raise ReportError(f"Вопрос {code} не найден.")
        if override.get("nets") is not None:
            question["nets"] = [
                {**net, "values": _net_values(question, live, net["values"])}
                for net in override["nets"]
            ]
        if override.get("scale_box") is not None:
            # Размер Top/Bottom — свой у каждого вопроса экрана: у одной
            # шкалы Top-2, у соседней Top-3. Книга отчёта его не видит.
            question["scale_box"] = int(override["scale_box"])
        if override.get("scale_inverted"):
            question["scale_inverted"] = True


def _net_values(question: dict[str, Any], live: dict[str, Any], values: list[Any]) -> list[Any]:
    """Коды ответов по подписям строк: экран знает строки подписями."""
    variables = {item["name"]: item for item in live["inspection"]["variables"]}
    sources = question.get("source_variables") or []
    if is_multiple(question):
        by_label = {
            option["label"]: option["key"] for option in response_options(question, variables)
        }
    elif len(sources) == 1 and sources[0] in variables:
        by_label = {
            item["label"]: item["value"] for item in variables[sources[0]].get("value_labels", [])
        }
    else:
        by_label = {}
    return [by_label.get(value, value) for value in values]


def _choose_questions(configuration: dict[str, Any], questions: list[str]) -> None:
    by_code = {item["code"]: item for item in configuration["questions"]}
    wanted = list(dict.fromkeys(questions))
    missing = [code for code in wanted if code not in by_code]
    if missing:
        raise ReportError("Вопросы не найдены: " + ", ".join(missing) + ".")
    unsupported = [
        code for code in wanted if by_code[code]["question_type"] not in LIVE_QUESTION_TYPES
    ]
    if unsupported:
        raise ReportError(
            "В таблицу нельзя поставить открытые, технические и пока не поддерживаемые "
            "вопросы: " + ", ".join(unsupported) + "."
        )
    for item in configuration["questions"]:
        item["included_in_report"] = item["code"] in wanted
    # Строки идут в порядке раскладки, а не в порядке структуры.
    configuration["questions"] = [by_code[code] for code in wanted] + [
        item for item in configuration["questions"] if item["code"] not in wanted
    ]


def _has_wave_column(configuration: dict[str, Any]) -> bool:
    active = next(
        (
            item
            for item in configuration.get("banners", [])
            if str(item.get("id")) == str(configuration.get("report_banner_id"))
        ),
        None,
    )
    if active is None:
        return False
    waves = {item["code"] for item in configuration["questions"] if item.get("role") == "wave"}
    return any(
        source.get("kind") == "question" and source.get("ref") in waves
        for block in active.get("blocks", [])
        for source in block.get("sources", [])
    )


__all__ = ["LIVE_QUESTION_TYPES", "build_live_table", "export_live_table"]
