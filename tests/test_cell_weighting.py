"""Взвешивание по ячейкам сочетаний (PQ.9).

Эталон считается вручную: вес ячейки — её цель, делённая на её долю в
выборке. Десять респондентов в ячейках 4, 1, 2 и 3 при целях по 25% дают
веса 10·0,25/4 = 0,625, 10·0,25/1 = 2,5, 10·0,25/2 = 1,25 и 10·0,25/3 = 0,8(3).
"""

from pathlib import Path

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.weighting import (
    WeightingError,
    calculate_cell_weighting,
    calculate_raking,
    calculate_weight,
)
from sav_analytics.repository import ProjectRepository

SEX = [1, 1, 1, 1, 1, 2, 2, 2, 2, 2]
AGE = [1, 1, 1, 1, 2, 1, 1, 2, 2, 2]


def _dimensions() -> list[dict]:
    return [
        {
            "variable": "SEX",
            "label": "Пол",
            "targets": [
                {"label": "М", "values": [1]},
                {"label": "Ж", "values": [2]},
            ],
        },
        {
            "variable": "AGE",
            "label": "Возраст",
            "targets": [
                {"label": "Молодые", "values": [1]},
                {"label": "Старшие", "values": [2]},
            ],
        },
    ]


def _definition(percents: dict[tuple[str, str], float], **extra) -> dict:
    return {
        "name": "Пол × возраст",
        "method": "cells",
        "dimensions": _dimensions(),
        "cells": [
            {"categories": list(key), "percent": percent} for key, percent in percents.items()
        ],
        "lower_bound": None,
        "upper_bound": None,
        **extra,
    }


EVEN = {("М", "Молодые"): 25, ("М", "Старшие"): 25, ("Ж", "Молодые"): 25, ("Ж", "Старшие"): 25}


def test_cell_weights_match_the_hand_calculation() -> None:
    frame = pd.DataFrame({"SEX": SEX, "AGE": AGE})

    result = calculate_cell_weighting(frame, _definition(EVEN))

    expected = [0.625] * 4 + [2.5] + [1.25] * 2 + [10 * 0.25 / 3] * 3
    assert result.weights.tolist() == pytest.approx(expected)
    assert result.weights.mean() == pytest.approx(1)
    cells = {cell["label"]: cell for cell in result.diagnostics["cells"]}
    assert cells["М × Старшие"]["weight"] == pytest.approx(2.5)
    assert cells["М × Старшие"]["before_percent"] == pytest.approx(10)
    # Цели категорий — суммы целей ячеек: 50/50 по полу и по возрасту.
    sex = result.diagnostics["distributions"][0]["categories"]
    assert [item["target_percent"] for item in sex] == pytest.approx([50, 50])
    assert [item["after_percent"] for item in sex] == pytest.approx([50, 50])


def test_cells_reach_a_joint_target_that_raking_cannot() -> None:
    """Ради этого ячейки и нужны: raking знает только маргиналы.

    В выборке ячейки 40/10/20/30, маргиналы пол 50/50 и возраст 60/40. Цель
    30/20/30/20 имеет те же маргиналы, поэтому raking оставит все веса
    равными единице и совместное распределение не сдвинет.
    """
    frame = pd.DataFrame({"SEX": SEX, "AGE": AGE})
    joint = {("М", "Молодые"): 30, ("М", "Старшие"): 20, ("Ж", "Молодые"): 30, ("Ж", "Старшие"): 20}

    cells = calculate_weight(frame, _definition(joint))
    weighted = cells.weights.groupby([frame["SEX"], frame["AGE"]]).sum() / cells.weights.sum()
    assert weighted.tolist() == pytest.approx([0.3, 0.2, 0.3, 0.2])

    raking = {
        **_definition(joint),
        "method": "raking",
        "dimensions": [
            {
                **dimension,
                "targets": [
                    {**target, "percent": percent}
                    for target, percent in zip(dimension["targets"], shares, strict=True)
                ],
            }
            for dimension, shares in zip(_dimensions(), ([50, 50], [60, 40]), strict=True)
        ],
    }
    assert calculate_raking(frame, raking).weights.tolist() == pytest.approx([1.0] * 10)


def test_cell_weight_outside_the_bounds_is_an_error_not_a_trim() -> None:
    frame = pd.DataFrame({"SEX": SEX, "AGE": AGE})

    with pytest.raises(WeightingError, match="М × Старшие.*2.500"):
        calculate_cell_weighting(frame, _definition(EVEN, lower_bound=0.3, upper_bound=2.0))


def test_empty_cell_with_a_target_is_named() -> None:
    frame = pd.DataFrame({"SEX": SEX, "AGE": [1, 1, 1, 1, 1, 1, 1, 2, 2, 2]})

    with pytest.raises(WeightingError, match="«М × Старшие» пуста"):
        calculate_cell_weighting(frame, _definition(EVEN))


def test_respondents_in_a_cell_with_zero_target_are_refused() -> None:
    frame = pd.DataFrame({"SEX": SEX, "AGE": AGE})
    targets = {**EVEN, ("М", "Старшие"): 0, ("М", "Молодые"): 50}

    with pytest.raises(WeightingError, match="У 1 респондентов ячейки «М × Старшие» цель 0%"):
        calculate_cell_weighting(frame, _definition(targets))


def test_cell_targets_must_sum_to_one_hundred() -> None:
    frame = pd.DataFrame({"SEX": SEX, "AGE": AGE})

    with pytest.raises(WeightingError, match="100%"):
        calculate_cell_weighting(frame, _definition({**EVEN, ("М", "Старшие"): 20}))


def test_cell_weight_is_saved_and_used_by_the_report(tmp_path: Path) -> None:
    source = tmp_path / "cells.sav"
    pyreadstat.write_sav(
        pd.DataFrame({"SEX": SEX, "AGE": AGE, "SCORE": list(range(10))}),
        source,
        column_labels={"SEX": "Пол", "AGE": "Возраст", "SCORE": "Оценка"},
        variable_value_labels={"SEX": {1: "М", 2: "Ж"}, "AGE": {1: "Молодые", 2: "Старшие"}},
        variable_measure={"SEX": "nominal", "AGE": "nominal", "SCORE": "scale"},
    )
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects", files={"file": ("cells.sav", stream, "application/octet-stream")}
            ).json()["id"]
            definition = _definition(EVEN)
            missing = client.post(
                f"/api/projects/{project_id}/weights", json={**definition, "cells": []}
            )
            assert missing.status_code == 422

            created = client.post(f"/api/projects/{project_id}/weights", json=definition)
            assert created.status_code == 201, created.text
            weight = created.json()["configuration"]["calculated_weights"][0]
            assert weight["method"] == "cells"

            preview = client.get(f"/api/projects/{project_id}/weights/{weight['id']}/preview")
            assert preview.status_code == 200
            assert preview.json()["method"] == "cells"
            assert len(preview.json()["cells"]) == 4

            settings = client.put(
                f"/api/projects/{project_id}/report-settings",
                json={"calculated_weight_id": weight["id"]},
            )
            assert settings.status_code == 200
            statistics = client.get(f"/api/projects/{project_id}/reports/preflight")
            assert statistics.status_code == 200
            assert not statistics.json()["errors"]
    finally:
        app.dependency_overrides.clear()


def _wave_project() -> dict:
    return {
        "configuration": {
            "questions": [{"code": "WAVE", "role": "wave", "source_variables": ["WAVE"]}]
        },
        "inspection": {
            "variables": [
                {
                    "name": "WAVE",
                    "value_labels": [
                        {"value": 1, "label": "Весна"},
                        {"value": 2, "label": "Осень"},
                    ],
                }
            ]
        },
    }


def test_weight_is_calculated_inside_each_wave() -> None:
    """§10: цели достигаются в каждой волне, а не только в сумме волн.

    Весной мужчин 80%, осенью 20%. Общий raking к 50/50 по всему массиву
    оставил бы волны 80/20 и 20/80 в сумме, и сравнение волн мерило бы состав.
    """
    frame = pd.DataFrame(
        {"WAVE": [1] * 10 + [2] * 10, "SEX": [1] * 8 + [2] * 2 + [1] * 2 + [2] * 8}
    )
    definition = {
        "name": "Пол",
        "dimensions": [
            {
                "variable": "SEX",
                "label": "Пол",
                "targets": [
                    {"label": "М", "values": [1], "percent": 50},
                    {"label": "Ж", "values": [2], "percent": 50},
                ],
            }
        ],
        "lower_bound": None,
        "upper_bound": None,
    }

    result = calculate_weight(frame, definition, _wave_project())

    for wave in (1, 2):
        part = frame["WAVE"] == wave
        men = result.weights[part & (frame["SEX"] == 1)].sum() / result.weights[part].sum()
        assert men == pytest.approx(0.5)
        assert result.weights[part].mean() == pytest.approx(1)
    assert [item["label"] for item in result.diagnostics["waves"]] == ["Весна", "Осень"]
    # Весной вес мужчины 0,5/0,8, осенью 0,5/0,2.
    assert result.weights.iloc[0] == pytest.approx(0.625)
    assert result.weights.iloc[10] == pytest.approx(2.5)


def test_wave_error_names_the_wave_and_missing_wave_is_refused() -> None:
    frame = pd.DataFrame({"WAVE": [1] * 4 + [2] * 4, "SEX": SEX[:4] + [2, 2, 2, 2], "AGE": AGE[:8]})
    definition = _definition(EVEN)

    with pytest.raises(WeightingError, match="Волна «Весна»"):
        calculate_weight(frame, definition, _wave_project())

    frame["WAVE"] = [1, 1, 1, 1, 2, 2, 2, None]
    with pytest.raises(WeightingError, match="не указана волна"):
        calculate_weight(frame, definition, _wave_project())
