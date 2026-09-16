"""Общие тесты блока: хи-квадрат Пирсона и Welch ANOVA (роадмап, PQ.6).

Эталон — SciPy: `chi2_contingency` без поправки на непрерывность и
`f_oneway(equal_var=False)`. Это независимая от приложения реализация.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pyreadstat
import pytest
from scipy.stats import chi2_contingency, f_oneway

from sav_analytics.core.report import build_statistics_txt, build_topline_xlsx
from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.core.statistics import chi_square_test, welch_anova
from tests.test_report import _cell_comment, _cell_value, _cell_values, _row_labels, declare_weight


def test_chi_square_matches_scipy() -> None:
    table = [[30, 20, 10], [20, 25, 30], [10, 15, 20]]

    result = chi_square_test(table, confidence_level=0.95, minimum_base=30)
    statistic, p_value, degrees, _ = chi2_contingency(np.array(table), correction=False)

    assert result.performed
    assert result.statistic == pytest.approx(statistic)
    assert result.p_value == pytest.approx(p_value)
    assert result.degrees_of_freedom == (degrees,)
    assert result.significant == (p_value < 0.05)


def test_chi_square_is_skipped_for_small_bases_and_expected_frequencies() -> None:
    small_base = chi_square_test([[10, 20], [5, 20]], confidence_level=0.95, minimum_base=30)
    assert not small_base.performed
    assert "База колонки" in small_base.reason

    rare = chi_square_test([[1, 1], [59, 59]], confidence_level=0.95, minimum_base=30)
    assert not rare.performed
    assert "Ожидаемые частоты малы" in rare.reason


def test_welch_anova_matches_scipy() -> None:
    generator = np.random.default_rng(7)
    groups = [
        generator.normal(5.0, 1.0, 40),
        generator.normal(5.6, 2.0, 55),
        generator.normal(4.8, 0.5, 35),
    ]

    result = welch_anova(groups, confidence_level=0.95, minimum_base=30)
    reference = f_oneway(*groups, equal_var=False)

    assert result.performed
    assert result.statistic == pytest.approx(reference.statistic)
    assert result.p_value == pytest.approx(reference.pvalue)


def test_welch_anova_is_skipped_without_spread() -> None:
    result = welch_anova([[3.0] * 40, [4.0, 5.0] * 20], confidence_level=0.95, minimum_base=30)

    assert not result.performed
    assert "разброса" in result.reason


def _project(source: Path, *, weighted: bool = False, **settings: object) -> dict:
    frame = pd.DataFrame(
        {
            "GROUP": [1] * 60 + [2] * 40,
            "OUTCOME": [1] * 42 + [2] * 18 + [1] * 20 + [2] * 20,
            "SCORE": [10 + index % 3 for index in range(60)]
            + [5 + index % 4 for index in range(40)],
            "W": [1.0, 1.2] * 50,
        }
    )
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"GROUP": "Группа", "OUTCOME": "Результат", "SCORE": "Оценка", "W": "Вес"},
        variable_value_labels={
            "GROUP": {1: "Первая", 2: "Вторая"},
            "OUTCOME": {1: "Да", 2: "Нет"},
        },
        variable_measure={"GROUP": "nominal", "OUTCOME": "nominal", "SCORE": "scale", "W": "scale"},
    )
    inspection = inspect_sav(source).to_dict()
    questions = declare_weight(inspection) if weighted else inspection["questions"]
    for question in questions:
        if question["code"] == "SCORE":
            question["question_type"] = "numeric"
        if question["code"] == "W" and not weighted:
            question["included_in_report"] = False
    report_settings = {
        "confidence_level": 0.95,
        "minimum_base": 30,
        "overall_tests": True,
        **settings,
    }
    if weighted:
        report_settings["weight_variable"] = "W"
    return {
        "name": "Общие тесты",
        "inspection": inspection,
        "configuration": {
            "questions": questions,
            "recodings": [],
            "filters": [],
            "report_filter_id": None,
            "banners": [
                {
                    "name": "Основной",
                    "blocks": [
                        {"label": "Группа", "sources": [{"kind": "question", "ref": "GROUP"}]}
                    ],
                }
            ],
            "report_settings": report_settings,
        },
    }


def test_workbook_writes_overall_tests_per_banner_block(tmp_path: Path) -> None:
    source = tmp_path / "overall.sav"
    project = _project(source)

    content = build_topline_xlsx(source, project)
    labels = _row_labels(content)

    assert "Хи-квадрат, p" in labels
    assert "Welch ANOVA, p" in labels
    _, chi_p, _, _ = chi2_contingency(np.array([[42, 20], [18, 20]]), correction=False)
    # p-value стоит в первой колонке блока: Total в B, «Первая» в C.
    # Первая строка — сам GROUP, он совпадает с баннером; OUTCOME — вторая.
    assert _cell_values(content, "Хи-квадрат, p", "C")[1] == pytest.approx(chi_p)
    first = [10 + index % 3 for index in range(60)]
    second = [5 + index % 4 for index in range(40)]
    welch_p = f_oneway(first, second, equal_var=False).pvalue
    assert _cell_value(content, "Welch ANOVA, p", "C") == pytest.approx(welch_p)
    assert "Метод: Хи-квадрат Пирсона" in (_cell_comment(content, "Хи-квадрат, p", "C") or "")

    audit = build_statistics_txt(source, project)
    assert "Общие тесты: хи-квадрат Пирсона" in audit
    assert "Метод: Welch ANOVA" in audit


def test_overall_tests_are_not_run_on_weighted_data(tmp_path: Path) -> None:
    source = tmp_path / "weighted.sav"
    content = build_topline_xlsx(source, _project(source, weighted=True))

    assert "Rao–Scott" in (_cell_comment(content, "Хи-квадрат, p", "C") or "")


def test_overall_tests_are_off_by_default(tmp_path: Path) -> None:
    source = tmp_path / "overall.sav"
    content = build_topline_xlsx(source, _project(source, overall_tests=False))

    assert "Хи-квадрат, p" not in _row_labels(content)
