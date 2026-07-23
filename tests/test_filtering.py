from pathlib import Path

from sav_analytics.core.filtering import calculate_filter_preview, validate_filter
from sav_analytics.core.sav_reader import inspect_sav
from tests.test_sav_reader import write_fixture


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
