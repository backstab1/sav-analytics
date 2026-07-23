from pathlib import Path

import pandas as pd
import pyreadstat

from sav_analytics.core.models import QuestionType, VariableRole
from sav_analytics.core.sav_reader import inspect_sav


def write_fixture(path: Path) -> None:
    frame = pd.DataFrame(
        {
            "id": [101, 102, 103, 104],
            "Q1": [1, 2, 1, 2],
            "Q2": [10, 9, 7, pd.NA],
            "Q3_1": [1, 0, 1, 0],
            "Q3_2": [0, 1, 1, 0],
            "Q4_open": ["Хорошо", "Быстро", "", "Удобно"],
        }
    ).astype({"Q2": "Float64"})
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={
            "id": "Номер интервью",
            "Q1": "Ваш пол?",
            "Q2": "Оцените сервис от 0 до 10",
            "Q3_1": "Что понравилось: скорость",
            "Q3_2": "Что понравилось: удобство",
            "Q4_open": "Почему вы поставили такую оценку?",
        },
        variable_value_labels={"Q1": {1: "Мужчина", 2: "Женщина"}},
        variable_measure={"id": "nominal", "Q1": "nominal", "Q2": "scale"},
    )


def test_inspection_reads_metadata_and_builds_questions(tmp_path: Path) -> None:
    source = tmp_path / "fixture.sav"
    write_fixture(source)

    result = inspect_sav(source)

    assert result.row_count == 4
    assert result.variable_count == 6
    assert next(item for item in result.variables if item.name == "id").role is VariableRole.ID
    assert next(item for item in result.variables if item.name == "Q1").value_labels[0].label
    scale = next(item for item in result.questions if item.code == "Q2")
    assert scale.question_type is QuestionType.SCALE
    assert scale.missing_count == 1
    multiple = next(item for item in result.questions if item.code == "Q3")
    assert multiple.question_type is QuestionType.MULTIPLE_DICHOTOMY
    assert multiple.source_variables == ["Q3_1", "Q3_2"]
    opened = next(item for item in result.questions if item.code == "Q4_open")
    assert opened.question_type is QuestionType.OPEN_TEXT
    assert not opened.included_in_report


def test_string_variables_with_numbered_suffix_are_not_forced_into_multiple(
    tmp_path: Path,
) -> None:
    source = tmp_path / "string_suffixes.sav"
    frame = pd.DataFrame(
        {
            "COMMENT_1": ["Первый ответ", "Второй ответ"],
            "COMMENT_2": ["Да", "Нет"],
        }
    )
    pyreadstat.write_sav(frame, source)

    result = inspect_sav(source)

    assert [question.code for question in result.questions] == ["COMMENT_1", "COMMENT_2"]
    assert all(
        question.question_type is QuestionType.OPEN_TEXT for question in result.questions
    )
