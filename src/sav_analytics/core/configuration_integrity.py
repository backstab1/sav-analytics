from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


class ConfigurationIntegrityError(ValueError):
    """Raised when a project change would leave dangling configuration links."""


@dataclass(frozen=True, slots=True)
class ConfigurationReference:
    target_kind: str
    target_id: str
    location: str


def find_references(
    configuration: dict[str, Any], target_kind: str, target_id: str
) -> list[ConfigurationReference]:
    """Find user-facing locations that reference one configuration object."""
    identifier = str(target_id)
    locations: list[str] = []
    if target_kind in {"question", "recoding"}:
        for holder in _source_holders(configuration):
            if any(
                source.get("kind") == target_kind and str(source.get("ref")) == identifier
                for source in holder.sources
            ):
                locations.append(holder.location)
        if target_kind == "question":
            locations.extend(
                f"сегментация «{_recoding_label(recoding)}»"
                for recoding in _segmentations(configuration)
                if identifier in recoding.get("variables", [])
            )
        if target_kind == "recoding":
            locations.extend(
                f"рассчитанный вес «{weight.get('name') or weight.get('id')}»"
                for weight in configuration.get("calculated_weights", [])
                if any(
                    str(dimension.get("recoding_id") or "") == identifier
                    for dimension in weight.get("dimensions", [])
                )
            )
    if target_kind == "filter":
        locations.extend(
            f"база вопроса {question.get('code')}"
            for question in configuration.get("questions", [])
            if question.get("base_filter_id") == identifier
        )
        if configuration.get("report_filter_id") == identifier:
            locations.append("общий фильтр отчёта")
    if target_kind == "calculated_weight":
        # Вес выбирается только в настройках отчёта. Копия на баннере осталась бы
        # от схемы 1 и блокировала удаление веса, который ни на что не влияет.
        if str(
            (configuration.get("report_settings") or {}).get("calculated_weight_id") or ""
        ) == identifier:
            locations.append("настройка отчёта")
    unique = dict.fromkeys(locations)
    return [ConfigurationReference(target_kind, identifier, location) for location in unique]


def ensure_not_referenced(
    configuration: dict[str, Any], target_kind: str, target_id: str, label: str
) -> None:
    references = find_references(configuration, target_kind, target_id)
    if not references:
        return
    locations = ", ".join(reference.location for reference in references)
    raise ConfigurationIntegrityError(
        f"{label} используется в конфигурации: {locations}. "
        "Сначала снимите связи или выберите замену."
    )


def validate_configuration_references(configuration: dict[str, Any]) -> None:
    """Reject a saved configuration containing dangling identifiers."""
    questions = {str(item.get("code")) for item in configuration.get("questions", [])}
    recodings = {str(item.get("id")) for item in configuration.get("recodings", [])}
    filters = {str(item.get("id")) for item in configuration.get("filters", [])}
    banners = {str(item.get("id")) for item in configuration.get("banners", [])}
    weights = {
        str(item.get("id")) for item in configuration.get("calculated_weights", [])
    }
    known = {"question": questions, "recoding": recodings}
    problems: list[str] = []

    for holder in _source_holders(configuration):
        if not holder.validated:
            continue
        for source in holder.sources:
            kind = source.get("kind")
            reference = str(source.get("ref"))
            if reference not in known.get(kind, ()):
                problems.append(
                    f"{holder.location} ссылается на отсутствующий источник {kind}:{reference}"
                )
    for recoding in _segmentations(configuration):
        missing = [code for code in recoding.get("variables", []) if code not in questions]
        if missing:
            problems.append(
                f"сегментация «{_recoding_label(recoding)}» ссылается на "
                f"отсутствующие вопросы {', '.join(missing)}"
            )
    for weight in configuration.get("calculated_weights", []):
        for dimension in weight.get("dimensions", []):
            recoding_id = dimension.get("recoding_id")
            if recoding_id and str(recoding_id) not in recodings:
                problems.append(
                    f"рассчитанный вес «{weight.get('name') or weight.get('id')}» ссылается "
                    f"на отсутствующую перекодировку {recoding_id}"
                )

    for question in configuration.get("questions", []):
        filter_id = question.get("base_filter_id")
        if filter_id and str(filter_id) not in filters:
            problems.append(
                f"вопрос {question.get('code')} ссылается на отсутствующую базу"
            )
    report_filter_id = configuration.get("report_filter_id")
    if report_filter_id and str(report_filter_id) not in filters:
        problems.append("общий фильтр отчёта не найден")
    report_banner_id = configuration.get("report_banner_id")
    if report_banner_id and str(report_banner_id) not in banners:
        problems.append("выбранный баннер отчёта не найден")
    report_weight_id = (configuration.get("report_settings") or {}).get(
        "calculated_weight_id"
    )
    if report_weight_id and str(report_weight_id) not in weights:
        problems.append("рассчитанный вес в настройках отчёта не найден")

    if problems:
        raise ConfigurationIntegrityError(
            "Конфигурация содержит повреждённые ссылки: " + "; ".join(problems) + "."
        )


@dataclass(frozen=True, slots=True)
class _SourceHolder:
    """Объект конфигурации, чьи источники — пары `{kind, ref}` на вопрос или
    перекодировку. Поиск ссылок и проверка целостности обходят один список."""

    location: str
    sources: list[dict[str, Any]]
    # Карточки «Анализа» учитываются при поиске ссылок, но при проверке
    # целостности не проверялись и до выноса этого списка.
    validated: bool = True


def _source_holders(configuration: dict[str, Any]) -> Iterator[_SourceHolder]:
    for banner in configuration.get("banners", []):
        yield _SourceHolder(
            f"баннер «{banner.get('name') or banner.get('id')}»",
            [source for block in banner.get("blocks", []) for source in block.get("sources", [])],
        )
    for definition in configuration.get("filters", []):
        yield _SourceHolder(
            f"фильтр «{definition.get('name') or definition.get('id')}»",
            list(_filter_sources(definition.get("rule", {}))),
        )
    for recoding in _conditional_recodings(configuration):
        yield _SourceHolder(
            f"логическая переменная «{_recoding_label(recoding)}»",
            [
                source
                for category in recoding.get("categories", [])
                for source in _filter_sources(category.get("rule") or {})
            ],
        )
    for model in configuration.get("analysis_models", []):
        yield _SourceHolder(
            "модель «Анализа»", [model.get("dependent", {}), *model.get("predictors", [])]
        )
    for card in configuration.get("analysis_cards", []):
        yield _SourceHolder(
            "карточка «Анализа»", [card.get("a", {}), card.get("b", {})], validated=False
        )


def _recoding_label(recoding: dict[str, Any]) -> str:
    return str(recoding.get("name") or recoding.get("code"))


def _segmentations(configuration: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in configuration.get("recodings", []) if item.get("mode") == "segments"]


def _conditional_recodings(configuration: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in configuration.get("recodings", []) if item.get("mode") == "conditions"
    ]


def _filter_sources(group: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for item in group.get("items", []):
        if item.get("kind") == "group":
            yield from _filter_sources(item)
        elif isinstance(item.get("source"), dict):
            yield item["source"]
