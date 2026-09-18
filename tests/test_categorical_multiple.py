"""Категориальный multiple-response (PQ.9, полная модель вопроса).

Слоты SLOT1 и SLOT2 хранят коды выбранных марок: у респондента в каждом слоте
записан один выбор. Вариант ответа — код, выбран он, если стоит хотя бы
в одном слоте.

    строка  SLOT1 SLOT2 SEX
    0       1     2     1     Альфа, Бета
    1       2     —     1     Бета
    2       1     3     1     Альфа, Гамма
    3       3     —     1     Гамма
    4       —     —     2     не ответил
    5       2     1     2     Бета, Альфа
    6       1     7     2     Альфа и неподписанный код 7
    7       —     —     2     не ответил

Ответили 6 из 8. Альфа — 4, Бета — 3, Гамма — 2, код 7 — 1.
"""

from pathlib import Path

import pandas as pd
import pyreadstat
import pytest

from sav_analytics.core.banner import calculate_banner_preview
from sav_analytics.core.filtering import condition_source_options, evaluate_filter_frame
from sav_analytics.core.multiple_response import (
    answered_mask,
    response_definition,
    response_options,
    selected_mask,
)
from sav_analytics.core.report import build_statistics_txt, build_topline_xlsx
from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.core.topline import calculate_preview
from tests.test_report import _cell_value, _row_labels

NAN = float("nan")


def _write(source: Path) -> None:
    frame = pd.DataFrame(
        {
            "SLOT1": [1, 2, 1, 3, NAN, 2, 1, NAN],
            "SLOT2": [2, NAN, 3, NAN, NAN, 1, 7, NAN],
            "SEX": [1, 1, 1, 1, 2, 2, 2, 2],
        }
    )
    brands = {1: "Альфа", 2: "Бета", 3: "Гамма"}
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={
            "SLOT1": "Марки — первый выбор",
            "SLOT2": "Марки — второй выбор",
            "SEX": "Пол",
        },
        variable_value_labels={
            "SLOT1": brands,
            "SLOT2": brands,
            "SEX": {1: "Мужчина", 2: "Женщина"},
        },
        variable_measure={name: "nominal" for name in frame},
    )


def _question(**extra: object) -> dict:
    return {
        "code": "BRANDS",
        "label": "Какие марки выбирали",
        "question_type": "multiple_choice_categorical",
        "role": "question",
        "source_variables": ["SLOT1", "SLOT2"],
        "valid_count": 6,
        "missing_count": 2,
        "included_in_report": True,
        "special_items": [],
        "multiple_response": {"encoding": "categorical", "counted_value": None},
        **extra,
    }


def _project(source: Path, question: dict | None = None, **configuration: object) -> dict:
    _write(source)
    inspection = inspect_sav(source).to_dict()
    sex = next(item for item in inspection["questions"] if item["code"] == "SEX")
    return {
        "name": "Категориальный multiple",
        "inspection": inspection,
        "configuration": {
            "questions": [question or _question(), dict(sex, included_in_report=False)],
            "recodings": [],
            "banners": [],
            "filters": [],
            "report_filter_id": None,
            **configuration,
        },
    }


def _frame(source: Path) -> pd.DataFrame:
    frame, _ = pyreadstat.read_sav(source, user_missing=False)
    return frame


def _variables(project: dict) -> dict[str, dict]:
    return {item["name"]: item for item in project["inspection"]["variables"]}


def test_options_are_labelled_codes_then_codes_seen_only_in_data(tmp_path: Path) -> None:
    source = tmp_path / "slots.sav"
    project = _project(source)
    question = project["configuration"]["questions"][0]

    without_data = response_options(question, _variables(project))
    with_data = response_options(question, _variables(project), _frame(source))

    assert [item["label"] for item in without_data] == ["Альфа", "Бета", "Гамма"]
    assert [item["label"] for item in with_data] == ["Альфа", "Бета", "Гамма", "7"]


def test_code_is_chosen_when_it_stands_in_any_slot(tmp_path: Path) -> None:
    source = tmp_path / "slots.sav"
    project = _project(source)
    question = project["configuration"]["questions"][0]
    frame = _frame(source)

    assert int(answered_mask(frame, question).sum()) == 6
    assert int(selected_mask(frame, question, 1).sum()) == 4
    assert int(selected_mask(frame, question, 2).sum()) == 3
    assert int(selected_mask(frame, question, 3).sum()) == 2
    # Код из JSON приходит и строкой, и 3.0 — это тот же вариант.
    assert int(selected_mask(frame, question, "3").sum()) == 2


def test_type_decides_the_encoding_even_if_stored_one_is_stale() -> None:
    """Группу перевели из дихотомии в категории — старое `encoding` не решает."""
    question = _question(multiple_response={"encoding": "dichotomy", "counted_value": 1})

    definition = response_definition(question)

    assert definition["encoding"] == "categorical"
    assert "counted_value" not in definition


def test_workbook_counts_each_code_once_per_respondent(tmp_path: Path) -> None:
    source = tmp_path / "slots.sav"
    project = _project(source)

    content = build_topline_xlsx(source, project)

    # topline_main — от всех 8 респондентов, topline_filter — от 6 ответивших.
    assert _cell_value(content, "Альфа", "B") == pytest.approx(4 / 8 * 100)
    assert _cell_value(content, "Бета", "B") == pytest.approx(3 / 8 * 100)
    assert _cell_value(content, "Гамма", "B") == pytest.approx(2 / 8 * 100)
    assert _cell_value(content, "7", "B") == pytest.approx(1 / 8 * 100)
    assert _cell_value(content, "Альфа", "B", sheet_index=2) == pytest.approx(4 / 6 * 100)


def test_not_applicable_code_is_neither_an_answer_nor_an_option(tmp_path: Path) -> None:
    source = tmp_path / "slots.sav"
    project = _project(source, _question(not_applicable_values=[7]))

    content = build_topline_xlsx(source, project)

    assert "7" not in _row_labels(content)
    # Строка 6 ответила Альфой, поэтому ответивших по-прежнему шесть.
    assert _cell_value(content, "Альфа", "B", sheet_index=2) == pytest.approx(4 / 6 * 100)


def test_net_is_built_from_codes(tmp_path: Path) -> None:
    source = tmp_path / "slots.sav"
    project = _project(
        source, _question(nets=[{"label": "Альфа или Гамма", "values": [1, "3"]}])
    )

    content = build_topline_xlsx(source, project)

    # Альфа — строки 0, 2, 5, 6; Гамма — 2, 3: вместе пять разных респондентов.
    assert _cell_value(content, "NET: Альфа или Гамма", "B") == pytest.approx(5 / 8 * 100)


def test_preview_shows_codes_and_explains_what_counts_as_chosen(tmp_path: Path) -> None:
    source = tmp_path / "slots.sav"
    project = _project(source)
    question = project["configuration"]["questions"][0]

    preview = calculate_preview(
        source, question, project["inspection"]["variables"], project
    )

    assert preview["valid_base"] == 6
    assert [(row["label"], row["count"]) for row in preview["rows"]] == [
        ("Альфа", 4),
        ("Бета", 3),
        ("Гамма", 2),
        ("7", 1),
    ]
    assert "хотя бы в одной" in preview["warnings"][0]


def test_categorical_multiple_gives_overlapping_banner_columns(tmp_path: Path) -> None:
    source = tmp_path / "slots.sav"
    banner = {
        "name": "Марки",
        "blocks": [{"label": "Марки", "sources": [{"kind": "question", "ref": "BRANDS"}]}],
    }
    project = _project(source, banners=[banner])

    preview = calculate_banner_preview(source, banner, project)

    assert [column["base"] for column in preview["columns"]] == [8, 4, 3, 2, 1]
    # Больше одной марки выбрали строки 0, 2, 5 и 6.
    assert preview["overlaps"] == [{"block_index": 0, "block": "Марки", "respondents": 4}]


def test_filter_condition_lists_codes_and_selects_by_them(tmp_path: Path) -> None:
    source = tmp_path / "slots.sav"
    project = _project(source)

    options = condition_source_options(
        source, {"kind": "question", "ref": "BRANDS"}, project
    )
    definition = {
        "name": "Альфа и Бета",
        "rule": {
            "kind": "group",
            "operator": "and",
            "items": [
                {
                    "kind": "condition",
                    "source": {"kind": "question", "ref": "BRANDS"},
                    "operator": "selected_all",
                    "values": [1, 2],
                }
            ],
        },
    }

    assert [(item["label"], item["count"]) for item in options["options"]][:3] == [
        ("Альфа", 4),
        ("Бета", 3),
        ("Гамма", 2),
    ]
    # Альфу и Бету вместе выбрали строки 0 и 5.
    assert int(evaluate_filter_frame(definition, project, _frame(source)).sum()) == 2


def test_code_rows_are_tested_like_any_other_row(tmp_path: Path) -> None:
    source = tmp_path / "slots.sav"
    banner = {
        "id": "sex",
        "name": "Пол",
        "blocks": [{"label": "Пол", "sources": [{"kind": "question", "ref": "SEX"}]}],
    }
    project = _project(
        source,
        banners=[banner],
        report_banner_id="sex",
        report_settings={
            "minimum_base": 1,
            "confidence_level": 0.95,
            "compare_to_total": True,
            "compare_target": "rest",
        },
    )

    audit = build_statistics_txt(source, project)

    assert "BRANDS" in audit
    assert "Альфа" in audit
