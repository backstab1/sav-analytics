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


def test_banner_rejects_open_text_question_as_source(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = project_fixture(source)
    invalid = {
        "name": "Ошибка",
        "blocks": [
            {"label": None, "sources": [{"kind": "question", "ref": "Q4_open"}]}
        ],
    }

    with pytest.raises(BannerError, match="single choice"):
        validate_banner(invalid, project)


def test_banner_requires_wave_role_for_wave_comparisons(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = project_fixture(source)
    invalid = {
        "name": "Волны",
        "wave_comparison": "previous",
        "blocks": [{"sources": [{"kind": "question", "ref": "Q1"}]}],
    }

    with pytest.raises(BannerError, match="ролью «Волна»"):
        validate_banner(invalid, project)


def test_banner_builds_control_wave_metadata(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = project_fixture(source)
    wave = next(item for item in project["configuration"]["questions"] if item["code"] == "Q1")
    wave["role"] = "wave"
    banner = {
        "name": "Волны",
        "wave_comparison": "control",
        "wave_control_value": 1,
        "blocks": [{"sources": [{"kind": "question", "ref": "Q1"}]}],
    }

    preview = calculate_banner_preview(source, banner, project)

    assert [column["wave_value"] for column in preview["columns"][1:]] == [1.0, 2.0]
    assert all(column["wave_comparison"] == "control" for column in preview["columns"][1:])


def test_banner_categories_are_reordered_renamed_and_hidden(tmp_path: Path) -> None:
    from sav_analytics.core.banner import calculate_banner_preview, source_category_options

    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = project_fixture(source)
    banner_source = {"kind": "question", "ref": "Q1"}
    options = source_category_options(source, banner_source, project)
    keys = [item["key"] for item in options["categories"]]
    assert [item["base"] for item in options["categories"]] == [2, 2]

    definition = {
        "name": "Пол",
        "blocks": [
            {
                "label": None,
                "sources": [
                    {
                        **banner_source,
                        "categories": [
                            {"key": keys[1], "label": "Женщины", "hidden": False},
                            {"key": keys[0], "label": None, "hidden": True},
                            {"key": "question:Q1:устарел", "label": "x", "hidden": False},
                        ],
                    }
                ],
            }
        ],
    }
    preview = calculate_banner_preview(source, definition, project)
    assert [column["label"] for column in preview["columns"]] == ["Total", "Женщины"]

    definition["blocks"][0]["sources"][0]["categories"] = [
        {"key": key, "label": None, "hidden": True} for key in keys
    ]
    with pytest.raises(BannerError, match="скрыты все категории"):
        calculate_banner_preview(source, definition, project)



def test_banner_categories_in_one_group_become_one_column(tmp_path: Path) -> None:
    """Редкие категории объединяются прямо в баннере, без новой перекодировки (PQ.5)."""
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = project_fixture(source)
    definition = {
        "name": "Оценка",
        "blocks": [
            {
                "label": None,
                "sources": [
                    {"kind": "question", "ref": "Q1"},
                    {
                        "kind": "recoding",
                        "ref": "score-groups",
                        "categories": [
                            {"key": "recoding:score-groups:1", "group": "Любая оценка"},
                            {"key": "recoding:score-groups:2", "group": "Любая оценка"},
                        ],
                    },
                ],
            }
        ],
    }

    preview = calculate_banner_preview(source, definition, project)

    columns = preview["columns"][1:]
    assert [column["path"][1] for column in columns] == ["Любая оценка", "Любая оценка"]
    # База группы — сумма баз её категорий: без группы было 1, 1 | 0, 1.
    assert [column["base"] for column in columns] == [2, 1]
    assert not any(column["overlapping"] for column in columns)
    assert len({column["key"] for column in columns}) == 2


def test_waves_are_not_merged_into_a_group(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = project_fixture(source)
    wave = next(item for item in project["configuration"]["questions"] if item["code"] == "Q1")
    wave["role"] = "wave"
    keys = ["question:Q1:1.0", "question:Q1:2.0"]
    definition = {
        "name": "Волны",
        "blocks": [
            {
                "sources": [
                    {
                        "kind": "question",
                        "ref": "Q1",
                        "categories": [{"key": key, "group": "Обе"} for key in keys],
                    }
                ]
            }
        ],
    }

    with pytest.raises(BannerError, match="Волны"):
        calculate_banner_preview(source, definition, project)


def test_wave_order_and_selection_follow_the_banner_categories(tmp_path: Path) -> None:
    """Порядок волн и их отключение задаются категориями баннера.

    «Предыдущая волна» — предыдущая колонка блока, поэтому переставленные
    категории меняют, с чем сравнивается волна, а скрытая волна выпадает из
    сравнения целиком.
    """
    from sav_analytics.core.banner import build_banner_columns
    from sav_analytics.core.formulas import read_project_frame
    from sav_analytics.core.reporting.statistics import _wave_target

    source = tmp_path / "fixture.sav"
    write_fixture(source)
    project = project_fixture(source)
    wave = next(item for item in project["configuration"]["questions"] if item["code"] == "Q1")
    wave["role"] = "wave"
    keys = ["question:Q1:1.0", "question:Q1:2.0"]
    definition = {
        "name": "Волны",
        "wave_comparison": "previous",
        "blocks": [
            {
                "sources": [
                    {
                        "kind": "question",
                        "ref": "Q1",
                        "categories": [{"key": keys[1]}, {"key": keys[0]}],
                    }
                ]
            }
        ],
    }
    frame = read_project_frame(source, project)

    columns = build_banner_columns(frame, definition, project)
    assert [column["wave_value"] for column in columns[1:]] == [2.0, 1.0]
    target = _wave_target(columns[2], columns, definition)
    assert target is columns[1]

    definition["blocks"][0]["sources"][0]["categories"][0]["hidden"] = True
    columns = build_banner_columns(frame, definition, project)
    assert [column["wave_value"] for column in columns[1:]] == [1.0]
    assert _wave_target(columns[1], columns, definition) is None
