from pathlib import Path

import pandas as pd
import pyreadstat

from sav_analytics.core.preflight import WIDE_BANNER_COLUMNS, run_preflight
from sav_analytics.core.sav_reader import inspect_sav


def _write_fixture(path: Path, rows: int = 100, groups: int = 2) -> None:
    frame = pd.DataFrame(
        {
            "GROUP": [1 + index % groups for index in range(rows)],
            "OUTCOME": [1 + index % 2 for index in range(rows)],
        }
    )
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={"GROUP": "Группа", "OUTCOME": "Результат"},
        variable_value_labels={
            "GROUP": {value: f"Группа {value}" for value in range(1, groups + 1)},
            "OUTCOME": {1: "Да", 2: "Нет"},
        },
        variable_measure={"GROUP": "nominal", "OUTCOME": "nominal"},
    )


def _project(inspection: dict, **configuration: object) -> dict:
    base = {
        "questions": inspection["questions"],
        "recodings": [],
        "filters": [],
        "banners": [],
        "report_filter_id": None,
    }
    base.update(configuration)
    return {"name": "Проверка", "inspection": inspection, "configuration": base}


def _codes(findings: list[dict]) -> set[str]:
    return {item["code"] for item in findings}


def test_clean_configuration_reports_nothing(tmp_path: Path) -> None:
    source = tmp_path / "clean.sav"
    _write_fixture(source)
    inspection = inspect_sav(source).to_dict()

    report = run_preflight(source, _project(inspection)).to_dict()

    assert report["can_prepare"] is True
    assert report["errors"] == []
    assert report["warnings"] == []


def test_empty_question_base_blocks_generation(tmp_path: Path) -> None:
    """Пустая база не ломает сборку — она даёт нули, неотличимые от честных."""
    source = tmp_path / "empty_base.sav"
    _write_fixture(source)
    inspection = inspect_sav(source).to_dict()
    questions = [dict(item) for item in inspection["questions"]]
    next(item for item in questions if item["code"] == "OUTCOME")["base_filter_id"] = "base-1"
    project = _project(
        inspection,
        questions=questions,
        filters=[
            {
                "id": "base-1",
                "name": "Несуществующая группа",
                "rule": {
                    "kind": "group",
                    "operator": "and",
                    "items": [
                        {
                            "kind": "condition",
                            "source": {"kind": "question", "ref": "GROUP"},
                            "operator": "eq",
                            "values": [99],
                        }
                    ],
                },
            }
        ],
    )

    report = run_preflight(source, project).to_dict()

    assert report["can_prepare"] is False
    assert _codes(report["errors"]) == {"EMPTY_QUESTION_BASE"}
    assert report["errors"][0]["scope"] == "OUTCOME"
    assert "Несуществующая группа" in report["errors"][0]["message"]


def test_empty_report_filter_is_a_blocking_error(tmp_path: Path) -> None:
    source = tmp_path / "empty_filter.sav"
    _write_fixture(source)
    inspection = inspect_sav(source).to_dict()
    project = _project(
        inspection,
        report_filter_id="filter-1",
        filters=[
            {
                "id": "filter-1",
                "name": "Пустой",
                "rule": {
                    "kind": "group",
                    "operator": "and",
                    "items": [
                        {
                            "kind": "condition",
                            "source": {"kind": "question", "ref": "GROUP"},
                            "operator": "eq",
                            "values": [99],
                        }
                    ],
                },
            }
        ],
    )

    report = run_preflight(source, project).to_dict()

    assert report["can_prepare"] is False
    assert _codes(report["errors"]) == {"REPORT_NOT_BUILDABLE"}
    assert "пустую выборку" in report["errors"][0]["message"]


def _weight_project(inspection: dict, *, declared: bool) -> dict:
    questions = [
        dict(question, role="weight", included_in_report=False)
        if declared and question["source_variables"] == ["W"]
        else question
        for question in inspection["questions"]
    ]
    return _project(
        inspection,
        questions=questions,
        banners=[
            {
                "id": "banner-1",
                "name": "Основной",
                "weight_variable": "W",
                "blocks": [{"sources": [{"kind": "question", "ref": "GROUP"}]}],
            }
        ],
        report_banner_id="banner-1",
    )


def test_unusable_weight_is_a_blocking_error(tmp_path: Path) -> None:
    """Нулевой вес блокирует сборку и называет свою причину, а не общую."""
    source = tmp_path / "bad_weight.sav"
    frame = pd.DataFrame({"GROUP": [1, 2] * 20, "W": [1.0, 0.0] * 20})
    pyreadstat.write_sav(
        frame, source, variable_value_labels={"GROUP": {1: "A", 2: "B"}}
    )
    inspection = inspect_sav(source).to_dict()

    report = run_preflight(source, _weight_project(inspection, declared=True)).to_dict()

    assert report["can_prepare"] is False
    assert _codes(report["errors"]) == {"WEIGHT_NOT_POSITIVE"}
    assert "положительными" in report["errors"][0]["message"]


def test_weight_without_the_weight_role_is_a_blocking_error(tmp_path: Path) -> None:
    """Не объявленная весом переменная не доходит до расчёта.

    Само распределение здесь безупречно: все веса равны единице. Отвергает его
    ровно отсутствие роли — то необходимое условие, которого не было до P1.0.
    """
    source = tmp_path / "undeclared_weight.sav"
    frame = pd.DataFrame({"GROUP": [1, 2] * 20, "W": [1.0] * 40})
    pyreadstat.write_sav(
        frame, source, variable_value_labels={"GROUP": {1: "A", 2: "B"}}
    )
    inspection = inspect_sav(source).to_dict()

    report = run_preflight(source, _weight_project(inspection, declared=False)).to_dict()

    assert report["can_prepare"] is False
    assert _codes(report["errors"]) == {"WEIGHT_ROLE_REQUIRED"}
    assert "не объявлена весом" in report["errors"][0]["message"]


def test_identifier_never_passes_as_a_weight(tmp_path: Path) -> None:
    """`ID` весом не проходит даже после явного объявления ролью.

    Это тот самый сценарий из аудита 14 августа. Роль здесь снята с проверки
    намеренно: остаётся диагностика распределения, и она обязана отбить
    порядковый номер сама. Design effect на таком весе всего 1,33 — ловит его
    доля экстремальных значений.
    """
    source = tmp_path / "identifier_weight.sav"
    rows = 240
    frame = pd.DataFrame(
        {
            "ID": [float(index + 1) for index in range(rows)],
            "GROUP": [1 + index % 2 for index in range(rows)],
        }
    )
    pyreadstat.write_sav(
        frame, source, variable_value_labels={"GROUP": {1: "A", 2: "B"}}
    )
    inspection = inspect_sav(source).to_dict()
    questions = [
        dict(question, role="weight", included_in_report=False)
        if question["source_variables"] == ["ID"]
        else question
        for question in inspection["questions"]
    ]
    project = _project(
        inspection,
        questions=questions,
        report_settings={"weight_variable": "ID"},
    )

    report = run_preflight(source, project).to_dict()

    assert report["can_prepare"] is False
    assert _codes(report["errors"]) == {"WEIGHT_EXTREME_VALUES"}


def test_small_and_empty_banner_categories_only_warn(tmp_path: Path) -> None:
    """Малая база заливается серым, нулевая не выводится — но обе не блокируют."""
    source = tmp_path / "small_base.sav"
    # Вторая группа даёт базу 5 при пороге 30, третья категория помечена в
    # value labels, но в данных не встречается ни разу.
    frame = pd.DataFrame({"GROUP": [1] * 95 + [2] * 5, "OUTCOME": [1] * 50 + [2] * 50})
    pyreadstat.write_sav(
        frame,
        source,
        variable_value_labels={
            "GROUP": {1: "Большая", 2: "Малая", 3: "Отсутствует"},
            "OUTCOME": {1: "Да", 2: "Нет"},
        },
        variable_measure={"GROUP": "nominal", "OUTCOME": "nominal"},
    )
    inspection = inspect_sav(source).to_dict()
    project = _project(
        inspection,
        banners=[
            {
                "id": "banner-1",
                "name": "Основной",
                "minimum_base": 30,
                "blocks": [{"sources": [{"kind": "question", "ref": "GROUP"}]}],
            }
        ],
        report_banner_id="banner-1",
    )

    report = run_preflight(source, project).to_dict()

    assert report["can_prepare"] is True
    assert report["errors"] == []
    assert _codes(report["warnings"]) == {"SMALL_COLUMN_BASE", "EMPTY_BANNER_CATEGORY"}
    empty = next(
        item for item in report["warnings"] if item["code"] == "EMPTY_BANNER_CATEGORY"
    )
    assert empty["scope"] == "Отсутствует"
    small = next(
        item for item in report["warnings"] if item["code"] == "SMALL_COLUMN_BASE"
    )
    assert "5" in small["message"]


def test_wide_banner_warns_from_fifty_columns(tmp_path: Path) -> None:
    source = tmp_path / "wide.sav"
    # Total плюс 49 категорий — ровно порог из requirements.md §6.
    _write_fixture(source, rows=490, groups=WIDE_BANNER_COLUMNS - 1)
    inspection = inspect_sav(source).to_dict()
    project = _project(
        inspection,
        banners=[
            {
                "id": "banner-1",
                "name": "Широкий",
                "minimum_base": 1,
                "blocks": [{"sources": [{"kind": "question", "ref": "GROUP"}]}],
            }
        ],
        report_banner_id="banner-1",
    )

    report = run_preflight(source, project).to_dict()

    assert report["can_prepare"] is True
    assert "WIDE_BANNER" in _codes(report["warnings"])
    assert str(WIDE_BANNER_COLUMNS) in next(
        item["message"] for item in report["warnings"] if item["code"] == "WIDE_BANNER"
    )


def test_report_without_included_questions_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "no_questions.sav"
    _write_fixture(source)
    inspection = inspect_sav(source).to_dict()
    questions = [dict(item, included_in_report=False) for item in inspection["questions"]]

    report = run_preflight(source, _project(inspection, questions=questions)).to_dict()

    assert report["can_prepare"] is False
    assert _codes(report["errors"]) == {"NO_QUESTIONS_INCLUDED"}
