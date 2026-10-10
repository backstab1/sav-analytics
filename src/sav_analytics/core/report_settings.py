from __future__ import annotations

from typing import Any

from .weight_validation import WEIGHT_ROLE, weight_role

# Показатели вывода в каноническом порядке. Порядок строк в книге задаёт он,
# а не порядок отметок: две одинаковые конфигурации дают один ключ кэша.
SCALE_METRICS = ("distribution", "mean", "top2", "bottom2")
NUMERIC_METRICS = ("mean", "median", "min", "max", "std", "stderr")

DEFAULT_REPORT_SETTINGS: dict[str, Any] = {
    "ranking_metrics": ["distribution", "mean"],
    "compare_to_total": False,
    "compare_target": "rest",
    "compare_pairwise": False,
    "confidence_level": 0.95,
    "bonferroni": False,
    "show_p_values": False,
    "note_skip_reasons": False,
    "minimum_base": 30,
    "weight_variable": None,
    "calculated_weight_id": None,
    "wave_comparison": "none",
    "wave_control_value": None,
    # Значения по умолчанию повторяют книгу до появления настроек: проект,
    # сохранённый раньше, собирается так же и открывается без миграции.
    "scale_metrics": list(SCALE_METRICS),
    "numeric_metrics": list(NUMERIC_METRICS),
    "percent_decimals": 0,
    "mean_decimals": 1,
    "scale_box": 2,
    "show_counts": False,
    "row_percents": False,
    "table_percents": False,
    "show_charts": False,
    "secondary_confidence_level": None,
    "overall_tests": False,
    "correlations": False,
    "counts_sheet": False,
    "presentation": False,
    # Свой готовый вес у волны: [{"wave": значение волны, "variable": имя}].
    # Остальные волны взвешиваются `weight_variable`.
    "wave_weights": [],
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

    _validate_wave_weights(settings, project)

    calculated_weight_id = settings.get("calculated_weight_id")
    if calculated_weight_id and not any(
        weight["id"] == str(calculated_weight_id)
        for weight in configuration.get("calculated_weights", [])
    ):
        raise ReportSettingsError("Рассчитанный вес не найден в проекте.")

    if settings.get("wave_comparison", "none") == "none":
        return
    # Волны выбираются в шапке: при одной волне сравнение идёт с соседней
    # волной, при сравнении волна сама становится колонкой. Баннер с волной
    # нужен, только если волн в проекте нет вовсе.
    if _has_waves(project):
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


def _validate_wave_weights(settings: dict[str, Any], project: dict[str, Any]) -> None:
    """Свой готовый вес у волны: та же роль «Вес», что у веса отчёта.

    Переопределение имеет смысл только рядом с готовым весом отчёта: у
    рассчитанного веса свои цели волны задаются в его редакторе.
    """
    overrides = settings.get("wave_weights") or []
    if not overrides:
        return
    if not settings.get("weight_variable"):
        raise ReportSettingsError(
            "Свой вес у волны задаётся рядом с готовым весом отчёта: сначала выберите его."
        )
    from .questionnaire import value_key
    from .waves import wave_values

    known = {value_key(item["value"]): item["label"] for item in wave_values(project)}
    names = {variable["name"] for variable in project["inspection"]["variables"]}
    seen: set[str] = set()
    for item in overrides:
        key = value_key(item.get("wave"))
        if key not in known:
            raise ReportSettingsError(f"Волны «{item.get('wave')}» в проекте нет.")
        if key in seen:
            raise ReportSettingsError(f"У волны «{known[key]}» вес задан дважды.")
        seen.add(key)
        variable = item.get("variable")
        if variable not in names:
            raise ReportSettingsError(f"Весовой переменной {variable} нет в SAV.")
        if weight_role(variable, project) != WEIGHT_ROLE:
            raise ReportSettingsError(
                f"Переменная {variable} не объявлена весом. "
                "Весом может быть только переменная с ролью «Вес»."
            )


def wave_weight_variables(settings: dict[str, Any]) -> list[str]:
    """Все переменные готового веса отчёта: общая и свои у волн."""
    names = [settings["weight_variable"]] if settings.get("weight_variable") else []
    for item in settings.get("wave_weights") or []:
        if item.get("variable") and item["variable"] not in names:
            names.append(item["variable"])
    return names


def _has_waves(project: dict[str, Any] | None) -> bool:
    from .waves import wave_values

    return len(wave_values(project)) >= 2


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
