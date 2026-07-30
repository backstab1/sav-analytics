from pathlib import Path

import pytest

from sav_analytics.core.banner import BannerError, calculate_banner_preview, validate_banner
from sav_analytics.core.sav_reader import inspect_sav
from tests.test_sav_reader import write_fixture


def project_fixture(path: Path) -> dict:
    inspection = inspect_sav(path).to_dict()
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


def test_banner_preview_keeps_total_and_builds_nested_columns(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = project_fixture(source)
    banner = {
        "name": "Демография",
        "compare_to_total": True,
        "compare_pairwise": True,
        "blocks": [
            {"label": "Пол", "sources": [{"kind": "question", "ref": "Q1"}]},
            {
                "label": "Пол и оценка",
                "sources": [
                    {"kind": "question", "ref": "Q1"},
                    {"kind": "recoding", "ref": "score-groups"},
                ],
            },
        ],
    }

    validate_banner(banner, project)
    preview = calculate_banner_preview(source, banner, project)

    assert preview["columns"][0] == {
        "key": "total",
        "label": "Total",
        "path": ["Total"],
        "base": 4,
        "block": None,
    }
    assert len(preview["columns"]) == 7
    assert [item["base"] for item in preview["columns"][3:]] == [1, 1, 0, 1]
    assert all(item["compare_to_total"] for item in preview["columns"][1:])
    assert all(item["compare_pairwise"] for item in preview["columns"][1:])


def test_banner_rejects_multiple_question_as_source(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = project_fixture(source)
    invalid = {
        "name": "Ошибка",
        "blocks": [
            {"label": None, "sources": [{"kind": "question", "ref": "Q3"}]}
        ],
    }

    with pytest.raises(BannerError, match="single choice"):
        validate_banner(invalid, project)
