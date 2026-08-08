from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyreadstat

from ..banner import build_banner_columns
from ..filtering import evaluate_filter_frame
from ..multiple_response import response_definition
from ..weighting import WeightingError, calculate_raking
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

    @property
    def total_steps(self) -> int:
        return len(self.questions) + len(self.filter_questions) + 2


def prepare_report_data(path: str | Path, project: dict[str, Any]) -> ReportData:
    frame, _ = pyreadstat.read_sav(
        path,
        apply_value_formats=False,
        user_missing=False,
        dates_as_pandas_datetime=False,
    )
    configuration = project["configuration"]
    global_mask = pd.Series(True, index=frame.index)
    report_filter_id = configuration.get("report_filter_id")
    if report_filter_id:
        definition = _find_by_id(configuration["filters"], report_filter_id, "Общий фильтр")
        global_mask &= evaluate_filter_frame(definition, project, frame)
        if not global_mask.any():
            raise ReportError("Общий фильтр отчёта даёт пустую выборку.")

    banners = configuration.get("banners", [])
    active_banner_id = configuration.get("report_banner_id")
    if "report_banner_id" not in configuration and banners:
        active_banner = banners[-1]
        columns = build_banner_columns(frame, active_banner, project)
    elif active_banner_id and banners:
        active_banner = next(
            (banner for banner in banners if banner.get("id") == active_banner_id), banners[-1]
        )
        columns = build_banner_columns(frame, active_banner, project)
    else:
        active_banner = {}
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
        column["mask"] = column["mask"] & global_mask
        column["base"] = int(column["mask"].sum())
    columns = [column for index, column in enumerate(columns) if index == 0 or column["base"]]

    questions = [
        question for question in configuration["questions"] if question["included_in_report"]
    ]
    unsupported = [
        question["code"]
        for question in questions
        if question["question_type"]
        in {"multiple_choice_categorical", "ranking"}
    ]
    if unsupported:
        raise ReportError(
            "Отчёт содержит пока не поддерживаемые типы вопросов: "
            + ", ".join(unsupported)
            + ". Исключите их из отчёта."
        )
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
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    filters = {item["id"]: item for item in configuration.get("filters", [])}
    filter_questions = [question for question in questions if question["missing_count"] > 0]
    weights, weight_label = _report_weights(
        frame,
        active_banner.get("weight_variable"),
        active_banner.get("calculated_weight_id"),
        configuration,
    )
    statistical_settings = {
        "confidence_level": active_banner.get("confidence_level", 0.95),
        "bonferroni": active_banner.get("bonferroni", False),
        "minimum_base": active_banner.get("minimum_base", 30),
        "weight_label": weight_label,
        "weights": weights,
        "wave_comparison": active_banner.get("wave_comparison", "none"),
        "wave_control_value": active_banner.get("wave_control_value"),
    }
    return ReportData(
        frame=frame,
        configuration=configuration,
        active_banner=active_banner,
        columns=columns,
        questions=questions,
        variables=variables,
        filters=filters,
        filter_questions=filter_questions,
        statistical_settings=statistical_settings,
    )


def _find_by_id(items: list[dict[str, Any]], identifier: str, label: str) -> dict[str, Any]:
    found = next((item for item in items if item["id"] == identifier), None)
    if found is None:
        raise ReportError(f"{label} не найден.")
    return found

def _report_weights(
    frame: pd.DataFrame,
    variable: str | None,
    calculated_weight_id: str | None,
    configuration: dict[str, Any],
) -> tuple[pd.Series | None, str | None]:
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
            result = calculate_raking(frame, definition)
        except WeightingError as exc:
            raise ReportError(str(exc)) from exc
        return result.weights, f"{definition['name']} (raking/IPF)"
    if not variable:
        return None, None
    if variable not in frame.columns:
        raise ReportError("Весовая переменная не найдена в SAV.")
    weights = pd.to_numeric(frame[variable], errors="coerce")
    if weights.isna().any():
        raise ReportError("Весовая переменная должна быть числовой и заполненной для всех строк.")
    if not weights.map(math.isfinite).all() or (weights <= 0).any():
        raise ReportError("Все веса должны быть конечными положительными числами.")
    normalized = weights.astype(float) / float(weights.mean())
    return normalized, variable
