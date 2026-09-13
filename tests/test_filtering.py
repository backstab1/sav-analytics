from pathlib import Path

from sav_analytics.core.filtering import (
    calculate_filter_preview,
    condition_source_options,
    describe_rule,
    validate_filter,
)
from sav_analytics.core.sav_reader import inspect_sav
from tests.test_sav_reader import write_counted_value_fixture, write_fixture


def test_filter_preview_supports_nested_rules_and_recoding(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    inspection = inspect_sav(source).to_dict()
    project = {
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [
                {
                    "id": "score-groups",
                    "mode": "ranges",
                    "code": "SCORE_GROUP",
                    "name": "Группы оценки",
                    "source_variable": "Q2",
                    "categories": [
                        {"label": "Низкая", "lower": 0, "upper": 7},
                        {"label": "Высокая", "lower": 8, "upper": 10},
                    ],
                }
            ],
        },
    }
    definition = {
        "name": "Мужчины или высокая оценка",
        "rule": {
            "kind": "group",
            "operator": "or",
            "items": [
                {
                    "kind": "condition",
                    "source": {"kind": "question", "ref": "Q1"},
                    "operator": "eq",
                    "values": [1],
                },
                {
                    "kind": "group",
                    "operator": "and",
                    "items": [
                        {
                            "kind": "condition",
                            "source": {"kind": "recoding", "ref": "score-groups"},
                            "operator": "eq",
                            "values": ["Высокая"],
                        }
                    ],
                },
            ],
        },
    }

    validate_filter(definition, project)
    preview = calculate_filter_preview(source, definition, project)

    assert preview["total"] == 4
    assert preview["selected"] == 3
    assert preview["share"] == 0.75
    assert "ИЛИ" in preview["description"]


def test_multiple_filter_uses_counted_value_and_excludes_missing_from_none(
    tmp_path: Path,
) -> None:
    source = tmp_path / "counted_value.sav"
    write_counted_value_fixture(source)
    inspection = inspect_sav(source).to_dict()
    multiple = {
        "code": "MR",
        "label": "Марки",
        "question_type": "multiple_choice_dichotomy",
        "source_variables": ["MR_1", "MR_2"],
        "multiple_response": {"encoding": "dichotomy", "counted_value": 2},
    }
    inspection["questions"] = [multiple]
    project = {
        "inspection": inspection,
        "configuration": {"questions": [multiple], "recodings": []},
    }

    def preview(operator: str) -> dict:
        definition = {
            "name": operator,
            "rule": {
                "kind": "group",
                "operator": "and",
                "items": [
                    {
                        "kind": "condition",
                        "source": {"kind": "question", "ref": "MR"},
                        "operator": operator,
                        "values": ["MR_1"],
                    }
                ],
            },
        }
        return calculate_filter_preview(source, definition, project)

    assert preview("selected")["selected"] == 2
    assert preview("selected_none")["selected"] == 1


def _condition(ref: str, operator: str, **fields: object) -> dict:
    return {
        "kind": "condition",
        "source": {"kind": "question", "ref": ref},
        "operator": operator,
        "values": fields.pop("values", []),
        **fields,
    }


def _fixture_project(source: Path) -> dict:
    write_fixture(source)
    inspection = inspect_sav(source).to_dict()
    return {
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"],
            "recodings": [
                {
                    "id": "score-groups",
                    "mode": "ranges",
                    "code": "SCORE_GROUP",
                    "name": "Группы оценки",
                    "source_variable": "Q2",
                    "categories": [
                        {"label": "Низкая", "lower": 0, "upper": 7},
                        {"label": "Высокая", "lower": 8, "upper": 10},
                    ],
                }
            ],
        },
    }


def test_rule_text_uses_value_labels_and_brackets_only_real_groups(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _fixture_project(source)
    rule = {
        "kind": "group",
        "operator": "and",
        "items": [
            _condition("Q1", "in", values=[2]),
            {
                "kind": "group",
                "operator": "or",
                "items": [_condition("Q2", "gt", lower=8), _condition("Q2", "missing")],
            },
            {
                "kind": "group",
                "operator": "or",
                "items": [_condition("Q1", "not_in", values=[1, 3])],
            },
        ],
    }

    # Коды заменены подписями, код без подписи остался числом; скобки стоят
    # только у группы из нескольких условий, «?» в конце вопроса отброшен.
    assert describe_rule(rule, project) == (
        "Ваш пол: Женщина"
        " И (Оцените сервис от 0 до 10 > 8 ИЛИ Оцените сервис от 0 до 10: пропуск)"
        " И Ваш пол: кроме Мужчина, 3"
    )
    preview = calculate_filter_preview(source, {"name": "Женщины", "rule": rule}, project)
    assert preview["description"] == describe_rule(rule, project)
    assert [step["running"] for step in preview["steps"] if "running" in step] == [2, 2, 2]


def test_preview_names_the_condition_that_emptied_the_sample(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _fixture_project(source)
    rule = {
        "kind": "group",
        "operator": "and",
        "items": [
            _condition("Q1", "in", values=[1]),
            _condition("Q1", "in", values=[2]),
            _condition("Q2", "filled"),
        ],
    }

    preview = calculate_filter_preview(source, {"name": "Пусто", "rule": rule}, project)

    assert preview["empty"]
    assert [step["running"] for step in preview["steps"]] == [2, 0, 0]
    # Под само условие подходят двое — обнулило выборку сочетание с предыдущим.
    assert preview["steps"][1]["selected"] == 2
    assert preview["emptied_after"] == "Ваш пол: Женщина"


def test_source_options_list_labels_with_counts_and_type_operators(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    project = _fixture_project(source)

    single = condition_source_options(source, {"kind": "question", "ref": "Q1"}, project)
    assert single["kind"] == "categorical"
    assert single["operators"] == ["in", "not_in", "filled", "missing"]
    assert [(item["label"], item["code"], item["count"]) for item in single["options"]] == [
        ("Мужчина", "1", 2),
        ("Женщина", "2", 2),
    ]

    recoded = condition_source_options(
        source, {"kind": "recoding", "ref": "score-groups"}, project
    )
    assert [(item["label"], item["count"]) for item in recoded["options"]] == [
        ("Низкая", 1),
        ("Высокая", 2),
    ]
    assert recoded["missing"] == 1


def test_source_options_for_multiple_drop_the_question_from_item_labels(
    tmp_path: Path,
) -> None:
    source = tmp_path / "counted_value.sav"
    write_counted_value_fixture(source)
    inspection = inspect_sav(source).to_dict()
    multiple = {
        "code": "MR",
        "label": "Марки",
        "question_type": "multiple_choice_dichotomy",
        "source_variables": ["MR_1", "MR_2"],
        "multiple_response": {"encoding": "dichotomy", "counted_value": 2},
    }
    project = {
        "inspection": inspection,
        "configuration": {"questions": [multiple], "recodings": []},
    }

    options = condition_source_options(source, {"kind": "question", "ref": "MR"}, project)

    assert options["kind"] == "multiple"
    assert [(item["label"], item["count"]) for item in options["options"]] == [
        ("Альфа", 2),
        ("Бета", 2),
    ]
    assert options["missing"] == 1
    rule = {"kind": "group", "operator": "and", "items": [
        _condition("MR", "selected_any", values=["MR_1"])
    ]}
    assert describe_rule(rule, project) == "Марки: выбран Альфа"
