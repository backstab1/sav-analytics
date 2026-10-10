from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..banner import BannerError, build_banner_columns
from ..configuration_integrity import (
    ConfigurationIntegrityError,
    validate_configuration_references,
)
from ..filtering import evaluate_filter_frame
from ..formulas import read_project_frame
from ..multiple_response import response_definition
from ..not_applicable import not_applicable_values
from ..questionnaire import value_key
from ..ranking import RankingError, ranking_items
from ..report_settings import resolved_report_settings
from ..waves import (
    compare_wave_block,
    comparison_wave,
    wave_variable_of,
    wave_view,
    with_wave_block,
)
from ..weight_validation import (
    assess_ready_weight,
    assess_weight_parts,
    ready_weight_parts,
    weight_role,
)
from ..weighting import WeightingError, calculate_weight, weight_method_label
from .models import ReportError


@dataclass(frozen=True)
class ReportData:
    frame: pd.DataFrame
    configuration: dict[str, Any]
    active_banner: dict[str, Any]
    columns: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    variables: dict[str, dict[str, Any]]
    filters: dict[str, dict[str, Any]]
    filter_questions: list[dict[str, Any]]
    statistical_settings: dict[str, Any]
    # Категории с нулевой базой в Excel не выводятся, но исчезать бесследно они
    # не должны: preflight предупреждает о них до запуска.
    empty_columns: list[str]

    @property
    def total_steps(self) -> int:
        return len(self.questions) + len(self.filter_questions) + 2


def prepare_report_data(
    path: str | Path, project: dict[str, Any], *, columns: list[str] | None = None
) -> ReportData:
    """Собрать данные отчёта.

    `columns` ограничивает чтение SAV нужными столбцами. Книге они нужны все,
    а таблице на экране — только вопросы раскладки, разрез, фильтр и вес,
    поэтому список считает вызывающий: он же знает раскладку.
    """
    # Режим «Сравнение волн»: волна — первый блок разреза везде, где
    # строится отчёт, без ручной настройки баннера.
    wave_block = compare_wave_block(project)
    configuration = project["configuration"]
    # Одна выбранная волна со сравнением «с предыдущей» или «с контрольной»:
    # читаются обе волны, колонки считаются по выбранной, а у каждой колонки
    # есть «тень» — та же подгруппа в волне сравнения, с ней идёт тест.
    shadow_wave = comparison_wave(
        project, resolved_report_settings(configuration, _active_banner(configuration))
    )
    wave_variable = wave_variable_of(project) if wave_block or shadow_wave else None
    if wave_variable and columns is not None and wave_variable not in columns:
        columns = [*columns, wave_variable]
    if shadow_wave is not None and wave_variable:
        active_value = wave_view(project)["value"]
        frame = read_project_frame(path, project, columns, all_waves=True)
        frame = frame[
            frame[wave_variable].map(
                lambda item: _same_value(item, active_value)
                or _same_value(item, shadow_wave["value"])
            )
        ]
        active_rows = frame[wave_variable].map(lambda item: _same_value(item, active_value))
    else:
        frame = read_project_frame(path, project, columns)
        active_rows = None
    try:
        validate_configuration_references(configuration)
    except ConfigurationIntegrityError as exc:
        raise ReportError(str(exc)) from exc
    filter_mask = _report_filter_mask(frame, project)
    global_mask = filter_mask if active_rows is None else filter_mask & active_rows

    active_banner = with_wave_block(_active_banner(configuration), wave_block)
    report_settings = resolved_report_settings(configuration, active_banner)
    if active_banner:
        # Comparison metadata belongs to the report, but banner-column building
        # still needs it to annotate each generated subgroup.
        active_banner = {**active_banner, **report_settings}
    report_columns, empty_columns = _report_columns(
        frame,
        active_banner,
        project,
        global_mask,
        shadow=(
            None if active_rows is None or shadow_wave is None
            else (filter_mask & ~active_rows, shadow_wave, report_settings["wave_comparison"])
        ),
    )

    questions = [
        question for question in configuration["questions"] if question["included_in_report"]
    ]
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    _check_report_questions(frame, questions, variables)
    filters = {item["id"]: item for item in configuration.get("filters", [])}
    # «Задавался не всем» — это и объявленный пропуск SPSS, и код, который
    # пользователь пометил как «не применимо»: для отчёта они равнозначны.
    filter_questions = [
        question
        for question in questions
        if question["missing_count"] > 0 or not_applicable_values(question)
    ]
    weights, weight_label = report_weights(
        frame,
        report_settings.get("weight_variable"),
        report_settings.get("calculated_weight_id"),
        project,
    )
    return ReportData(
        frame=frame,
        configuration=configuration,
        active_banner=active_banner,
        columns=report_columns,
        questions=questions,
        variables=variables,
        filters=filters,
        filter_questions=filter_questions,
        statistical_settings=_statistical_settings(
            report_settings, questions, weights, weight_label
        ),
        empty_columns=empty_columns,
    )


def _report_filter_mask(frame: pd.DataFrame, project: dict[str, Any]) -> pd.Series:
    configuration = project["configuration"]
    global_mask = pd.Series(True, index=frame.index)
    report_filter_id = configuration.get("report_filter_id")
    if report_filter_id:
        definition = _find_by_id(configuration["filters"], report_filter_id, "Общий фильтр")
        global_mask &= evaluate_filter_frame(definition, project, frame)
        if not global_mask.any():
            raise ReportError("Общий фильтр отчёта даёт пустую выборку.")
    return global_mask


def _active_banner(configuration: dict[str, Any]) -> dict[str, Any]:
    """Баннер отчёта; проект без явного выбора берёт последний сохранённый."""
    banners = configuration.get("banners", [])
    active_banner_id = configuration.get("report_banner_id")
    if "report_banner_id" not in configuration and banners:
        return banners[-1]
    if active_banner_id and banners:
        return next(
            (banner for banner in banners if banner.get("id") == active_banner_id), banners[-1]
        )
    return {}


def _same_value(item: Any, value: Any) -> bool:
    try:
        return bool(float(item) == float(value))
    except (TypeError, ValueError):
        return str(item) == str(value)


def _report_columns(
    frame: pd.DataFrame,
    active_banner: dict[str, Any],
    project: dict[str, Any],
    global_mask: pd.Series,
    *,
    shadow: tuple[pd.Series, dict[str, Any], str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Колонки под общим фильтром и подписи тех, что остались без базы.

    `shadow` — маска строк волны сравнения, сама волна и режим: у каждой
    колонки появляется `wave_shadow` с той же подгруппой в этой волне.
    Тени в книгу не выводятся, это только цель теста изменения.
    """
    if active_banner:
        try:
            columns = build_banner_columns(frame, active_banner, project)
        except BannerError as exc:
            raise ReportError(str(exc)) from exc
    else:
        columns = [
            {
                "key": "total",
                "label": "Total",
                "path": ["Total"],
                "base": len(frame),
                "block": None,
                "mask": pd.Series(True, index=frame.index),
            }
        ]
    for column in columns:
        if shadow is not None:
            shadow_rows, wave, mode = shadow
            shadow_mask = column["mask"] & shadow_rows
            column["wave_shadow"] = {
                **{key: value for key, value in column.items() if key != "mask"},
                "mask": shadow_mask,
                "base": int(shadow_mask.sum()),
                "label": f"{wave['label']} · {column['label']}",
                "wave_value": wave["value"],
            }
            column["wave_comparison"] = mode
        column["mask"] = column["mask"] & global_mask
        column["base"] = int(column["mask"].sum())
    empty_columns = [
        column["label"] for index, column in enumerate(columns) if index and not column["base"]
    ]
    columns = [column for index, column in enumerate(columns) if index == 0 or column["base"]]
    return columns, empty_columns


def _check_report_questions(
    frame: pd.DataFrame,
    questions: list[dict[str, Any]],
    variables: dict[str, dict[str, Any]],
) -> None:
    for question in questions:
        if question["question_type"] == "ranking":
            try:
                ranking_items(frame, question, variables)
            except RankingError as exc:
                raise ReportError(str(exc)) from exc
    invalid_multiple = [
        question["code"]
        for question in questions
        if question["question_type"] == "multiple_choice_dichotomy"
        and (
            response_definition(question).get("encoding") != "dichotomy"
            or response_definition(question).get("counted_value") is None
        )
    ]
    if invalid_multiple:
        raise ReportError(
            "Не задан код выбранного ответа multiple-response: "
            + ", ".join(invalid_multiple)
            + "."
        )


def _statistical_settings(
    report_settings: dict[str, Any],
    questions: list[dict[str, Any]],
    weights: pd.Series | None,
    weight_label: str | None,
) -> dict[str, Any]:
    return {
        "confidence_level": report_settings["confidence_level"],
        "bonferroni": report_settings["bonferroni"],
        "show_p_values": report_settings["show_p_values"],
        "note_skip_reasons": report_settings.get("note_skip_reasons", False),
        "minimum_base": report_settings["minimum_base"],
        "weight_label": weight_label,
        "weights": weights,
        "wave_comparison": report_settings["wave_comparison"],
        "wave_control_value": report_settings.get("wave_control_value"),
        "scale_metrics": tuple(report_settings["scale_metrics"]),
        "ranking_metrics": tuple(report_settings["ranking_metrics"]),
        "ranking_present": any(q["question_type"] == "ranking" for q in questions),
        "numeric_metrics": tuple(report_settings["numeric_metrics"]),
        "percent_decimals": report_settings["percent_decimals"],
        "mean_decimals": report_settings["mean_decimals"],
        "scale_box": report_settings["scale_box"],
        "show_counts": report_settings["show_counts"],
        "row_percents": report_settings["row_percents"],
        "table_percents": report_settings["table_percents"],
        "show_charts": report_settings["show_charts"],
        "secondary_confidence_level": report_settings["secondary_confidence_level"],
        "overall_tests": report_settings["overall_tests"],
        "correlations": report_settings["correlations"],
        "counts_sheet": report_settings["counts_sheet"],
        "presentation": report_settings["presentation"],
    }


def _find_by_id(items: list[dict[str, Any]], identifier: str, label: str) -> dict[str, Any]:
    found = next((item for item in items if item["id"] == identifier), None)
    if found is None:
        raise ReportError(f"{label} не найден.")
    return found

def report_weights(
    frame: pd.DataFrame,
    variable: str | None,
    calculated_weight_id: str | None,
    project: dict[str, Any],
) -> tuple[pd.Series | None, str | None]:
    configuration = project["configuration"]
    if variable and calculated_weight_id:
        raise ReportError("Выберите готовый или рассчитанный вес, но не оба сразу.")
    if calculated_weight_id:
        definition = next(
            (
                item
                for item in configuration.get("calculated_weights", [])
                if item["id"] == str(calculated_weight_id)
            ),
            None,
        )
        if definition is None:
            raise ReportError("Рассчитанный вес не найден в проекте.")
        try:
            result = calculate_weight(frame, definition, project)
        except WeightingError as exc:
            raise ReportError(str(exc)) from exc
        return result.weights, f"{definition['name']} ({weight_method_label(definition)})"
    if not variable:
        return None, None
    settings = configuration.get("report_settings") or {}
    if settings.get("wave_weights"):
        return _wave_ready_weights(frame, {**settings, "weight_variable": variable}, project)
    if variable not in frame.columns:
        raise ReportError("Весовая переменная не найдена в SAV.", code="WEIGHT_VARIABLE_NOT_FOUND")
    # Ту же оценку до сборки делают интерфейс и API. Здесь она стоит последним
    # рубежом: проект мог быть отредактирован в обход интерфейса или сохранён
    # до появления проверки.
    assessment = assess_ready_weight(
        frame[variable],
        variable=variable,
        role=weight_role(variable, project),
    )
    if not assessment.usable:
        # Наружу отдаётся `ReportError` с кодом первой проблемы: сборка и
        # preflight ловят один тип ошибки, но причина отказа не смазывается в
        # общий `REPORT_NOT_BUILDABLE`.
        problem = assessment.problems[0]
        raise ReportError(
            " ".join(item.message for item in assessment.problems), code=problem.code
        )
    weights = pd.to_numeric(frame[variable], errors="coerce").astype(float)
    normalized = weights / float(weights.mean())
    return normalized, variable


def _wave_ready_weights(
    frame: pd.DataFrame, settings: dict[str, Any], project: dict[str, Any]
) -> tuple[pd.Series, str]:
    """Готовый вес, у части волн — своя переменная.

    Каждая часть проверяется на своих строках тем же валидатором, а вес
    нормируется к среднему 1 внутри каждой волны, как рассчитанный
    (`requirements.md` §10): разные переменные бывают в разных масштабах, и
    общая нормировка отдала бы волне с крупными весами лишнюю долю.
    """
    try:
        parts = ready_weight_parts(frame, settings, project)
    except KeyError as exc:
        raise ReportError("Переменная волны не прочитана из SAV.") from exc
    for assessment in assess_weight_parts(frame, settings, project):
        if not assessment.usable:
            problem = assessment.problems[0]
            raise ReportError(
                " ".join(item.message for item in assessment.problems), code=problem.code
            )
    weights = pd.Series(float("nan"), index=frame.index)
    for part in parts:
        weights[part.mask] = pd.to_numeric(frame.loc[part.mask, part.variable], errors="coerce")
    wave = wave_variable_of(project)
    for _, rows in weights.groupby(frame[wave].map(value_key)):
        weights[rows.index] = rows / float(rows.mean())
    own = [f"у «{part.wave}» — {part.variable}" for part in parts if part.wave is not None]
    return weights.astype(float), f"{settings['weight_variable']}; " + ", ".join(own)
