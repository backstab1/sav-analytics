from pathlib import Path

from sav_analytics.core.filtering import calculate_filter_preview, validate_filter
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
