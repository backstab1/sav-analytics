from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pyreadstat

from sav_analytics.core.models import QuestionType, VariableRole
from sav_analytics.core.sav_reader import (
    _build_questions,
    _inspect_variable,
    _read_multiple_response_sets,
    inspect_sav,
)


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


def write_grouped_fixture(path: Path) -> None:
    frame = pd.DataFrame(
        {
            "Q5_1": [1, 2, 99, 2],
            "Q5_2": [2, 1, 2, 99],
            "Q6_1": [1, 0, 1, 0],
            "Q6_2": [0, 1, 0, 0],
            "Q6_99": [0, 0, 1, 1],
        }
    )
    scale_labels = {1: "Плохо", 2: "Хорошо", 99: "Затрудняюсь ответить"}
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={
            "Q5_1": "Оцените сервис: Скорость",
            "Q5_2": "Оцените сервис: Удобство",
            "Q6_1": "Что понравилось: Скорость",
            "Q6_2": "Что понравилось: Удобство",
            "Q6_99": "Что понравилось: Затрудняюсь ответить",
        },
        variable_value_labels={"Q5_1": scale_labels, "Q5_2": scale_labels},
        variable_measure={"Q5_1": "scale", "Q5_2": "scale"},
    )


def write_counted_value_fixture(path: Path) -> None:
    frame = pd.DataFrame(
        {
            "MR_1": [2, 1, 2, float("nan")],
            "MR_2": [1, 2, 2, float("nan")],
        }
    )
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={"MR_1": "Марки: Альфа", "MR_2": "Марки: Бета"},
        variable_value_labels={
            "MR_1": {1: "Не выбрано", 2: "Выбрано"},
            "MR_2": {1: "Не выбрано", 2: "Выбрано"},
        },
        variable_measure={"MR_1": "nominal", "MR_2": "nominal"},
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


def test_unlabelled_string_is_open_text_even_with_few_unique_answers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "few_open_answers.sav"
    frame = pd.DataFrame(
        {
            "Q_OPEN": ["Скучный", "Не нравится", "Скучный", "Не знаю"],
            "Q_CATEGORY": ["A", "B", "A", "B"],
        }
    )
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={
            "Q_OPEN": "Расскажите подробнее",
            "Q_CATEGORY": "Выберите вариант",
        },
        variable_value_labels={"Q_CATEGORY": {"A": "Вариант A", "B": "Вариант B"}},
        variable_measure={"Q_OPEN": "nominal", "Q_CATEGORY": "nominal"},
    )

    result = inspect_sav(source)

    opened = next(question for question in result.questions if question.code == "Q_OPEN")
    category = next(
        question for question in result.questions if question.code == "Q_CATEGORY"
    )
    assert opened.question_type is QuestionType.OPEN_TEXT
    assert not opened.included_in_report
    assert category.question_type is QuestionType.SINGLE_CHOICE
    assert category.included_in_report


def test_numbered_scales_become_matrix_and_detect_special_answers(tmp_path: Path) -> None:
    source = tmp_path / "grouped.sav"
    write_grouped_fixture(source)

    result = inspect_sav(source)

    matrix = next(question for question in result.questions if question.code == "Q5")
    assert matrix.question_type is QuestionType.MATRIX
    assert matrix.source_variables == ["Q5_1", "Q5_2"]
    assert matrix.special_values == [99.0]
    assert len(matrix.items) == 2

    multiple = next(question for question in result.questions if question.code == "Q6")
    assert multiple.question_type is QuestionType.MULTIPLE_DICHOTOMY
    assert multiple.special_items == ["Q6_99"]


def test_sparse_numbered_response_slots_are_not_mistaken_for_scales(
    tmp_path: Path,
) -> None:
    source = tmp_path / "positional_categories.sav"
    frame = pd.DataFrame(
        {
            "CHOICE_1": [*range(1, 16), *([float("nan")] * 5)],
            "CHOICE_2": [1, 2, 3, 4, 5, *([float("nan")] * 15)],
        }
    )
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"CHOICE_1": "Первый выбор", "CHOICE_2": "Второй выбор"},
        variable_measure={"CHOICE_1": "scale", "CHOICE_2": "scale"},
    )

    result = inspect_sav(source)

    questions = {question.code: question for question in result.questions}
    assert questions["CHOICE_1"].question_type is QuestionType.SINGLE_CHOICE
    assert questions["CHOICE_2"].question_type is QuestionType.SINGLE_CHOICE
    assert not any(question.code == "CHOICE" for question in result.questions)
    assert any("позицион" in warning for warning in result.warnings)


def test_metadata_multiple_response_preserves_counted_value_and_rowwise_base() -> None:
    frame = pd.DataFrame(
        {
            "M1": [2, float("nan"), 2, float("nan")],
            "M2": [float("nan"), 2, 1, float("nan")],
        }
    )
    metadata = SimpleNamespace(
        column_names_to_labels={"M1": "Марки: Альфа", "M2": "Марки: Бета"},
        variable_value_labels={},
        original_variable_types={},
        variable_measure={"M1": "nominal", "M2": "nominal"},
        missing_ranges={},
        missing_user_values={},
        mr_sets={
            "$BRANDS": {
                "type": "D",
                "is_dichotomy": True,
                "counted_value": 2,
                "label": "Марки",
                "variable_list": ["M1", "M2"],
            }
        },
    )
    variables = [_inspect_variable(frame[name], name, metadata) for name in frame]
    response_sets = _read_multiple_response_sets(metadata)

    questions, _ = _build_questions(frame, variables, response_sets, metadata)

    question = questions[0]
    assert question.question_type is QuestionType.MULTIPLE_DICHOTOMY
    assert question.multiple_response == {
        "encoding": "dichotomy",
        "counted_value": 2,
        "source": "spss_metadata",
    }
    assert question.valid_count == 3
    assert question.missing_count == 1


def test_metadata_categorical_multiple_is_excluded_until_supported() -> None:
    frame = pd.DataFrame({"SLOT1": [1, 2], "SLOT2": [2, 1]})
    metadata = SimpleNamespace(
        column_names_to_labels={},
        variable_value_labels={},
        original_variable_types={},
        variable_measure={},
        missing_ranges={},
        missing_user_values={},
        mr_sets={
            "$CHOICES": {
                "type": "C",
                "is_dichotomy": False,
                "counted_value": None,
                "label": "Выборы",
                "variable_list": ["SLOT1", "SLOT2"],
            }
        },
    )
    variables = [_inspect_variable(frame[name], name, metadata) for name in frame]

    questions, _ = _build_questions(
        frame, variables, _read_multiple_response_sets(metadata), metadata
    )

    question = questions[0]
    assert question.question_type is QuestionType.MULTIPLE_CATEGORICAL
    assert not question.included_in_report
    assert any("не поддерживается" in warning for warning in question.warnings)
