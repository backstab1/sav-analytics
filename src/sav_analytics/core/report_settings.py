from __future__ import annotations

from typing import Any

from .weight_validation import WEIGHT_ROLE, weight_role

DEFAULT_REPORT_SETTINGS: dict[str, Any] = {
    "compare_to_total": False,
    "compare_target": "rest",
    "compare_pairwise": False,
    "confidence_level": 0.95,
    "bonferroni": False,
    "show_p_values": False,
    "minimum_base": 30,
    "weight_variable": None,
    "calculated_weight_id": None,
    "wave_comparison": "none",
    "wave_control_value": None,
}

REPORT_SETTING_KEYS = tuple(DEFAULT_REPORT_SETTINGS)


class ReportSettingsError(ValueError):
    pass


def validate_report_settings(settings: dict[str, Any], project: dict[str, Any]) -> None:
    configuration = project["configuration"]
    weight_variable = settings.get("weight_variable")
    if weight_variable and not any(
        variable["name"] == weight_variable
        for variable in project["inspection"]["variables"]
    ):
        raise ReportSettingsError("Весовая переменная не найдена в SAV.")
    # Роль проверяется здесь, потому что для неё не нужен массив: этот путь
    # проходят и старые клиенты, присылающие вес на баннере. Распределение
    # проверяет роутер — там есть исходный файл.
    if weight_variable and weight_role(weight_variable, project) != WEIGHT_ROLE:
        raise ReportSettingsError(
            f"Переменная {weight_variable} не объявлена весом. "
            "Весом может быть только переменная с ролью «Вес»."
        )

    calculated_weight_id = settings.get("calculated_weight_id")
    if calculated_weight_id and not any(
        weight["id"] == str(calculated_weight_id)
        for weight in configuration.get("calculated_weights", [])
    ):
        raise ReportSettingsError("Рассчитанный вес не найден в проекте.")

    if settings.get("wave_comparison", "none") == "none":
        return
    active_banner_id = configuration.get("report_banner_id")
    active_banner = next(
        (
            banner
            for banner in configuration.get("banners", [])
            if banner.get("id") == active_banner_id
        ),
        None,
    )
    wave_questions = {
        question["code"]
        for question in configuration.get("questions", [])
        if question.get("role") == "wave"
    }
    has_wave_column = active_banner and any(
        source.get("kind") == "question" and source.get("ref") in wave_questions
        for block in active_banner.get("blocks", [])
        for source in block.get("sources", [])
    )
    if not has_wave_column:
        raise ReportSettingsError(
            "Для сравнения волн выберите для Excel баннер с переменной в роли «Волна»."
        )


def resolved_report_settings(
    configuration: dict[str, Any],
    active_banner: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return global settings, falling back to the active legacy banner.

    Older project files stored these values on every banner. Keeping this
    reader-side migration lets them open without a destructive schema rewrite.
    """

    settings = DEFAULT_REPORT_SETTINGS.copy()
    legacy = active_banner or {}
    for key in REPORT_SETTING_KEYS:
        if key in legacy:
            settings[key] = legacy[key]
    if "compare_to_total" not in legacy:
        settings["compare_to_total"] = any(
            block.get("compare_to_total", False) for block in legacy.get("blocks", [])
        )
    if "compare_pairwise" not in legacy:
        settings["compare_pairwise"] = any(
            block.get("compare_pairwise", False) for block in legacy.get("blocks", [])
        )
    settings.update(configuration.get("report_settings") or {})
    return settings
