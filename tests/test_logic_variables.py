"""Логическая переменная: категории по правилам, респондент в первой подходящей."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.banner import calculate_banner_preview
from sav_analytics.core.configuration_integrity import find_references
from sav_analytics.core.filtering import calculate_filter_preview
from sav_analytics.core.recoding import (
    RecodingError,
    calculate_recode_preview,
    validate_recode,
)
from sav_analytics.core.reporting.live import build_live_table
from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture


def _condition(ref: str, operator: str, **fields: object) -> dict:
    return {
        "kind": "condition",
        "source": {"kind": "question", "ref": ref},
        "operator": operator,
        "values": fields.pop("values", []),
        **fields,
    }


def _rule(*items: dict, operator: str = "and") -> dict:
    return {"kind": "group", "operator": operator, "items": list(items)}


SEGMENT = {
    "id": "segment",
    "mode": "conditions",
    "code": "SEGMENT",
    "name": "Сегмент",
    "categories": [
        # Первая подходящая категория забирает респондента: мужчина с оценкой
        # выше 8 во «Все мужчины» уже не попадает.
        {"label": "Довольные мужчины", "rule": _rule(
            _condition("Q1", "in", values=[1]), _condition("Q2", "gt", lower=8)
        )},
        {"label": "Все мужчины", "rule": _rule(_condition("Q1", "in", values=[1]))},
        {"label": "Женщины", "rule": _rule(_condition("Q1", "in", values=[2]))},
    ],
}


def _project(source: Path) -> dict:
    write_fixture(source)
    inspection = inspect_sav(source).to_dict()
    return {
        "name": "Логика",
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [dict(SEGMENT)],
            "banners": [],
            "filters": [],
            "report_filter_id": None,
        },
    }


def test_each_respondent_lands_in_the_first_matching_category(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _project(source)

    validate_recode(SEGMENT, project["inspection"]["variables"], project)
    preview = calculate_recode_preview(source, SEGMENT, project)

    # Q1 = 1, 2, 1, 2; Q2 = 10, 9, 7, пропуск.
    assert [(row["label"], row["count"]) for row in preview["rows"]] == [
        ("Довольные мужчины", 1),
        ("Все мужчины", 1),
        ("Женщины", 2),
    ]
    assert preview["out_of_range_count"] == 0


OTHERWISE = {
    "id": "promoters",
    "mode": "conditions",
    "code": "PROMO",
    "name": "Довольные",
    "categories": [
        {"label": "Довольные мужчины", "rule": _rule(
            _condition("Q1", "in", values=[1]), _condition("Q2", "gt", lower=8)
        )},
        # «Иначе» забирает всех, кто не подошёл выше, включая пропуск в Q2.
        {"label": "Остальные", "otherwise": True, "rule": None},
    ],
}


def test_otherwise_takes_everyone_left(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _project(source)
    project["configuration"]["recodings"].append(dict(OTHERWISE))

    validate_recode(OTHERWISE, project["inspection"]["variables"], project)
    preview = calculate_recode_preview(source, OTHERWISE, project)
    blocks = [{"label": "Довольные", "sources": [{"kind": "recoding", "ref": "promoters"}]}]
    banner = calculate_banner_preview(source, {"name": "Довольные", "blocks": blocks}, project)

    assert [(row["label"], row["count"]) for row in preview["rows"]] == [
        ("Довольные мужчины", 1),
        ("Остальные", 3),
    ]
    assert preview["out_of_range_count"] == 0
    assert [column["base"] for column in banner["columns"]] == [4, 1, 3]


def test_otherwise_stands_last_and_not_alone(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _project(source)
    variables = project["inspection"]["variables"]
    otherwise = {"label": "Остальные", "otherwise": True, "rule": None}

    first = {**OTHERWISE, "categories": [otherwise, OTHERWISE["categories"][0]]}
    with pytest.raises(RecodingError, match="только последней"):
        validate_recode(first, variables, project)

    alone = {**OTHERWISE, "categories": [otherwise, {**otherwise, "label": "Все"}]}
    with pytest.raises(RecodingError, match="только последней"):
        validate_recode(alone, variables, project)


def test_logic_variable_cuts_a_banner_and_a_table(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _project(source)
    blocks = [{"label": "Сегмент", "sources": [{"kind": "recoding", "ref": "segment"}]}]

    banner = calculate_banner_preview(source, {"name": "Сегмент", "blocks": blocks}, project)
    table = build_live_table(source, project, questions=["Q1"], blocks=blocks)

    assert [column["base"] for column in banner["columns"]] == [4, 1, 1, 2]
    assert [column["base"] for column in table["columns"]] == [4, 1, 1, 2]


def test_logic_variable_can_be_a_filter_condition(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _project(source)
    rule = {
        "kind": "group",
        "operator": "and",
        "items": [
            {
                "kind": "condition",
                "source": {"kind": "recoding", "ref": "segment"},
                "operator": "in",
                "values": ["Женщины"],
            }
        ],
    }

    preview = calculate_filter_preview(source, {"name": "Женщины", "rule": rule}, project)

    assert preview["selected"] == 2
    assert preview["description"] == "Сегмент: Женщины"


def test_logic_variable_refuses_duplicates_and_chains(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _project(source)
    variables = project["inspection"]["variables"]

    duplicate = {**SEGMENT, "id": None, "categories": [
        {"label": "А", "rule": _rule(_condition("Q1", "in", values=[1]))},
        {"label": "а", "rule": _rule(_condition("Q1", "in", values=[2]))},
    ]}
    with pytest.raises(RecodingError, match="не должны повторяться"):
        validate_recode(duplicate, variables, project)

    chained = {**SEGMENT, "id": "other", "code": "OTHER", "categories": [
        {"label": "Из сегмента", "rule": {"kind": "group", "operator": "and", "items": [{
            "kind": "condition",
            "source": {"kind": "recoding", "ref": "segment"},
            "operator": "in",
            "values": ["Женщины"],
        }]}},
        {"label": "Прочие", "rule": _rule(_condition("Q1", "in", values=[1]))},
    ]}
    with pytest.raises(RecodingError, match="другую логическую переменную"):
        validate_recode(chained, variables, project)


def test_rules_of_a_logic_variable_count_as_references(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _project(source)

    references = find_references(project["configuration"], "question", "Q2")

    assert [reference.location for reference in references] == [
        "логическая переменная «Сегмент»"
    ]


def test_logic_variable_through_the_api(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                files={"file": ("research.sav", stream, "application/octet-stream")},
            ).json()
            base = f"/api/projects/{project['id']}/recodings"
            ranges = client.post(
                base,
                json={
                    "mode": "ranges",
                    "code": "SCORE_GROUP",
                    "name": "Оценка",
                    "source_variable": "Q2",
                    "categories": [
                        {"label": "Низкая", "lower": 0, "upper": 8},
                        {"label": "Высокая", "lower": 9, "upper": 10},
                    ],
                },
            )
            assert ranges.status_code == 201
            ranges_id = ranges.json()["configuration"]["recodings"][0]["id"]

            payload = {
                "mode": "conditions",
                "code": "SEGMENT",
                "name": "Сегмент",
                "categories": [
                    {"label": "Высокая оценка", "rule": {"operator": "and", "items": [{
                        "source": {"kind": "recoding", "ref": ranges_id},
                        "operator": "in",
                        "values": ["Высокая"],
                    }]}},
                    {"label": "Остальные с оценкой", "rule": {"operator": "and", "items": [{
                        "source": {"kind": "question", "ref": "Q2"},
                        "operator": "filled",
                    }]}},
                ],
            }
            created = client.post(base, json=payload)
            assert created.status_code == 201
            segment = next(
                item
                for item in created.json()["configuration"]["recodings"]
                if item["code"] == "SEGMENT"
            )
            preview = client.get(f"{base}/{segment['id']}/preview")
            assert preview.status_code == 200
            assert [row["count"] for row in preview.json()["rows"]] == [2, 1]
            assert preview.json()["out_of_range_count"] == 1

            # «Иначе» без условия: API принимает её только последней.
            otherwise = {**payload, "code": "REST", "categories": [
                payload["categories"][0],
                {"label": "Остальные", "otherwise": True},
            ]}
            created = client.post(base, json=otherwise)
            assert created.status_code == 201
            rest = next(
                item
                for item in created.json()["configuration"]["recodings"]
                if item["code"] == "REST"
            )
            counts = client.get(f"{base}/{rest['id']}/preview").json()
            assert [row["count"] for row in counts["rows"]] == [2, 2]
            misplaced = {**otherwise, "code": "BAD", "categories": otherwise["categories"][::-1]}
            assert client.post(base, json=misplaced).status_code == 422
            ruleless = {**payload, "code": "BAD", "categories": [
                payload["categories"][0], {"label": "Без условия"},
            ]}
            assert client.post(base, json=ruleless).status_code == 422

            # Группировка, на которой стоит правило, не удаляется молча.
            refused = client.delete(f"{base}/{ranges_id}")
            assert refused.status_code == 422
            assert "логическая переменная «Сегмент»" in refused.json()["detail"]
    finally:
        app.dependency_overrides.clear()
