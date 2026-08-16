"""Пригодность готового веса — доменный слой.

Сценарий из аудита 14 августа 2026 стоит первым: `ID` не должен становиться
весом ни по роли, ни по распределению.
"""

from pathlib import Path

import pandas as pd
import pyreadstat
import pytest

from sav_analytics.core.weight_validation import (
    DESIGN_EFFECT_LIMIT,
    WeightNotUsableError,
    assess_project_weight,
    assess_ready_weight,
    ensure_project_weight_usable,
    weight_role,
)


def _codes(assessment) -> list[str]:
    return [problem.code for problem in assessment.problems]


def _assess(values: list[float], *, role: str | None = "weight", variable: str = "W"):
    return assess_ready_weight(pd.Series(values), variable=variable, role=role)


def test_plausible_weight_passes_and_reports_its_distribution() -> None:
    """Вес 2,0 у четверти выборки: правдоподобная поправка, а не поломка.

    Эффективная база считается вручную: сумма 100, сумма квадратов 140,
    100² / 140 = 71,43 при 80 наблюдениях, значит design effect 1,12.
    """
    assessment = _assess([2.0] * 20 + [1.0] * 60)

    assert assessment.usable
    assert assessment.problems == []
    diagnostics = assessment.diagnostics
    assert diagnostics is not None
    assert diagnostics.count == 80
    assert (diagnostics.minimum, diagnostics.maximum) == (1.0, 2.0)
    assert diagnostics.mean == pytest.approx(1.25)
    assert diagnostics.extreme_count == 0
    assert diagnostics.effective_base == pytest.approx(71.4285714, rel=1e-6)
    assert diagnostics.design_effect == pytest.approx(1.12, rel=1e-6)
    assert diagnostics.efficiency_percent == pytest.approx(89.2857142, rel=1e-6)


def test_identifier_is_rejected_by_role_before_any_arithmetic() -> None:
    assessment = _assess([float(index + 1) for index in range(240)], role="id", variable="ID")

    assert not assessment.usable
    assert _codes(assessment)[0] == "WEIGHT_ROLE_REQUIRED"
    assert "не объявлена весом" in assessment.problems[0].message
    assert "Идентификатор" in assessment.problems[0].message


def test_identifier_is_rejected_by_its_distribution_even_when_declared() -> None:
    """Роль без диагностики недостаточна — это условие решения 5.1 разбора аудита.

    Порядковый номер 1…240 даёт design effect всего 1,33: веса растут ровно, и
    эффективная база почти не страдает. Отвергают его экстремальные значения —
    24 респондента из 240 весят меньше пятой доли среднего.
    """
    assessment = _assess([float(index + 1) for index in range(240)], variable="ID")

    assert not assessment.usable
    assert _codes(assessment) == ["WEIGHT_EXTREME_VALUES"]
    diagnostics = assessment.diagnostics
    assert diagnostics is not None
    assert diagnostics.extreme_count == 24
    assert diagnostics.extreme_share_percent == pytest.approx(10.0)
    assert diagnostics.design_effect == pytest.approx(1.3307, rel=1e-3)
    assert diagnostics.design_effect < DESIGN_EFFECT_LIMIT


def test_heavy_tail_is_rejected_by_design_effect_alone() -> None:
    """Четверо респондентов с весом 20 против 96 с весом 0,5.

    Доля экстремальных значений здесь всего 4% — под порогом, — а design effect
    9,9: сотня интервью работает как десять. Порог долей такой вес пропустил бы,
    поэтому проверки две. Эффективная база считается вручную:
    128² / 1624 = 10,09.
    """
    assessment = _assess([20.0] * 4 + [0.5] * 96)

    assert _codes(assessment) == ["WEIGHT_DESIGN_EFFECT"]
    diagnostics = assessment.diagnostics
    assert diagnostics is not None
    assert diagnostics.extreme_count == 4
    assert diagnostics.extreme_share_percent == pytest.approx(4.0)
    assert diagnostics.effective_base == pytest.approx(10.0887, rel=1e-4)
    assert diagnostics.design_effect == pytest.approx(9.9124, rel=1e-4)
    assert diagnostics.design_effect > DESIGN_EFFECT_LIMIT


def test_zero_and_negative_weights_are_rejected_without_diagnostics() -> None:
    assessment = _assess([1.0, 0.0, -2.0, 1.0])

    assert _codes(assessment) == ["WEIGHT_NOT_POSITIVE"]
    assert "3" not in assessment.problems[0].message  # значений ровно два
    assert assessment.diagnostics is None


def test_missing_and_non_numeric_values_are_named_separately() -> None:
    missing = assess_ready_weight(
        pd.Series([1.0, None, 1.0]), variable="W", role="weight"
    )
    assert _codes(missing) == ["WEIGHT_HAS_MISSING"]

    text = assess_ready_weight(
        pd.Series(["много", "мало", "1.0"]), variable="W", role="weight"
    )
    assert _codes(text) == ["WEIGHT_NOT_NUMERIC"]
    assert "2 значений" in text.problems[0].message


def test_constant_weight_passes_with_a_note() -> None:
    """Вес из одних единиц не ошибка, но и не вес: отчёт совпадёт с невзвешенным."""
    assessment = _assess([1.0] * 50)

    assert assessment.usable
    assert assessment.notes and "постоянный" in assessment.notes[0]


def test_role_comes_from_the_configuration_not_from_the_inspection() -> None:
    """Роль правит аналитик; автоопределение остаётся в `inspection` и устаревает."""
    project = {
        "inspection": {"variables": [{"name": "W_FINAL", "role": "question"}]},
        "configuration": {
            "questions": [
                {"code": "W_FINAL", "source_variables": ["W_FINAL"], "role": "weight"}
            ]
        },
    }

    assert weight_role("W_FINAL", project) == "weight"
    assert weight_role("MISSING", project) is None


def test_project_weight_is_read_from_the_source_and_can_raise(tmp_path: Path) -> None:
    source = tmp_path / "weight.sav"
    pyreadstat.write_sav(
        pd.DataFrame({"GROUP": [1.0, 2.0] * 20, "W": [1.2, 0.8] * 20}), source
    )
    declared = {
        "inspection": {"variables": []},
        "configuration": {
            "questions": [{"code": "W", "source_variables": ["W"], "role": "weight"}]
        },
    }

    assessment = ensure_project_weight_usable(source, "W", declared)
    assert assessment.usable
    assert assessment.diagnostics is not None
    assert assessment.diagnostics.count == 40

    undeclared = {
        "inspection": {"variables": []},
        "configuration": {
            "questions": [{"code": "W", "source_variables": ["W"], "role": "question"}]
        },
    }
    with pytest.raises(WeightNotUsableError) as failure:
        ensure_project_weight_usable(source, "W", undeclared)
    assert failure.value.code == "WEIGHT_ROLE_REQUIRED"

    absent = assess_project_weight(source, "NOPE", declared)
    assert _codes(absent) == ["WEIGHT_VARIABLE_NOT_FOUND"]
