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
    references: list[ConfigurationReference] = []
    if target_kind in {"question", "recoding"}:
        for banner in configuration.get("banners", []):
            for block in banner.get("blocks", []):
                for source in block.get("sources", []):
                    if (
                        source.get("kind") == target_kind
                        and str(source.get("ref")) == identifier
                    ):
                        references.append(
                            ConfigurationReference(
                                target_kind,
                                identifier,
                                f"баннер «{banner.get('name') or banner.get('id')}»",
                            )
                        )

        for definition in configuration.get("filters", []):
            if any(
                source.get("kind") == target_kind
                and str(source.get("ref")) == identifier
                for source in _filter_sources(definition.get("rule", {}))
            ):
                references.append(
                    ConfigurationReference(
                        target_kind,
                        identifier,
                        f"фильтр «{definition.get('name') or definition.get('id')}»",
                    )
                )
    if target_kind == "filter":
        for question in configuration.get("questions", []):
            if question.get("base_filter_id") == identifier:
                references.append(
                    ConfigurationReference(
                        target_kind,
                        identifier,
                        f"база вопроса {question.get('code')}",
                    )
                )
        if configuration.get("report_filter_id") == identifier:
            references.append(
                ConfigurationReference(
                    target_kind, identifier, "общий фильтр отчёта"
                )
            )
    if target_kind == "calculated_weight":
        for banner in configuration.get("banners", []):
            if str(banner.get("calculated_weight_id") or "") == identifier:
                references.append(
                    ConfigurationReference(
                        target_kind,
                        identifier,
                        f"баннер «{banner.get('name') or banner.get('id')}»",
                    )
                )
    return _unique_references(references)


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
    problems: list[str] = []

    for banner in configuration.get("banners", []):
        banner_label = banner.get("name") or banner.get("id")
        for block in banner.get("blocks", []):
            for source in block.get("sources", []):
                kind = source.get("kind")
                reference = str(source.get("ref"))
                known = questions if kind == "question" else recodings
                if kind not in {"question", "recoding"} or reference not in known:
                    problems.append(
                        f"баннер «{banner_label}» ссылается на отсутствующий источник "
                        f"{kind}:{reference}"
                    )
        weight_id = banner.get("calculated_weight_id")
        if weight_id and str(weight_id) not in weights:
            problems.append(
                f"баннер «{banner_label}» ссылается на отсутствующий рассчитанный вес"
            )

    for definition in configuration.get("filters", []):
        filter_label = definition.get("name") or definition.get("id")
        for source in _filter_sources(definition.get("rule", {})):
            kind = source.get("kind")
            reference = str(source.get("ref"))
            known = questions if kind == "question" else recodings
            if kind not in {"question", "recoding"} or reference not in known:
                problems.append(
                    f"фильтр «{filter_label}» ссылается на отсутствующий источник "
                    f"{kind}:{reference}"
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

    if problems:
        raise ConfigurationIntegrityError(
            "Конфигурация содержит повреждённые ссылки: " + "; ".join(problems) + "."
        )


def _filter_sources(group: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for item in group.get("items", []):
        if item.get("kind") == "group":
            yield from _filter_sources(item)
        elif isinstance(item.get("source"), dict):
            yield item["source"]


def _unique_references(
    references: list[ConfigurationReference],
) -> list[ConfigurationReference]:
    result: list[ConfigurationReference] = []
    seen: set[tuple[str, str, str]] = set()
    for reference in references:
        key = (reference.target_kind, reference.target_id, reference.location)
        if key not in seen:
            seen.add(key)
            result.append(reference)
    return result
