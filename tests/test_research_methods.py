"""Методы исследования (PQ.15): TURF, Van Westendorp, Gabor–Granger.

Эталоны — расчёт вручную на маленьких массивах и независимая реализация
на чистом Python без numpy.
"""

from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.research_methods import (
    _best_exact,
    _best_greedy,
    _Patterns,
    gabor_granger,
    turf,
    van_westendorp,
)
from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.repository import ProjectRepository

# Семь респондентов, три варианта: A — 1 и 2, B — 2 и 3, C — 4, 5 и 6, седьмой — ничего.
BUY = {
    "BUY_1": [1, 1, 0, 0, 0, 0, 0],
    "BUY_2": [0, 1, 1, 0, 0, 0, 0],
    "BUY_3": [0, 0, 0, 1, 1, 1, 0],
}


def _project(path: Path, frame: pd.DataFrame, extra: list[dict] | None = None) -> dict:
    pyreadstat.write_sav(frame, path)
    inspection = inspect_sav(path).to_dict()
    questions = [
        question
        for question in inspection["questions"]
        if not any(name in BUY for name in question.get("source_variables") or [])
    ]
    for question in questions:
        if question["code"].startswith(("P_", "G_")):
            question["question_type"] = (
                "numeric" if question["code"].startswith("P_") else "single_choice"
            )
    questions.extend(extra or [])
    return {
        "inspection": inspection,
        "configuration": {
            "questions": questions,
            "recodings": [],
            "filters": [],
            "report_settings": {},
        },
    }


BUY_QUESTION = {
    "code": "BUY",
    "label": "Что купите",
    "question_type": "multiple_choice_dichotomy",
    "role": "question",
    "source_variables": list(BUY),
    "multiple_response": {"encoding": "dichotomy", "counted_value": 1},
}


def test_turf_matches_the_hand_count(tmp_path: Path) -> None:
    project = _project(tmp_path / "turf.sav", pd.DataFrame(BUY), [BUY_QUESTION])
    variables = project["inspection"]["variables"]
    for variable in variables:
        variable["label"] = {"BUY_1": "A", "BUY_2": "B", "BUY_3": "C"}.get(
            variable["name"], variable["label"]
        )

    result = turf(tmp_path / "turf.sav", project, "BUY", max_size=3)

    assert result["base"] == 7
    one, two, three = result["portfolios"]
    assert (one["items"], one["reach"]) == (["C"], pytest.approx(3 / 7))
    # A+C и B+C охватывают по пятеро; при равной частоте берётся первый по анкете.
    assert (two["items"], two["reach"]) == (["A", "C"], pytest.approx(5 / 7))
    assert three["reach"] == pytest.approx(6 / 7)
    assert three["frequency"] == pytest.approx(7 / 7)


def test_exact_and_greedy_turf_agree_with_brute_force() -> None:
    generator = np.random.default_rng(4)
    chosen = generator.random((300, 9)) < 0.25
    weights = generator.uniform(0.5, 1.5, 300)
    patterns = _Patterns(chosen, weights)

    def brute(size: int) -> float:
        return max(
            sum(weights[row] for row in range(300) if any(chosen[row, index] for index in items))
            / weights.sum()
            for items in combinations(range(9), size)
        )

    for size in (1, 2, 3):
        assert _best_exact(patterns, 9, size)["reach"] == pytest.approx(brute(size))
    # Жадный отбор не лучше точного и на первом шаге с ним совпадает.
    assert _best_greedy(patterns, 9, 1)["reach"] == pytest.approx(brute(1))
    assert _best_greedy(patterns, 9, 3)["reach"] <= brute(3) + 1e-12


def _price_frame() -> pd.DataFrame:
    generator = np.random.default_rng(8)
    size = 80
    too_cheap = generator.integers(20, 60, size).astype(float)
    cheap = too_cheap + generator.integers(5, 30, size)
    expensive = cheap + generator.integers(10, 40, size)
    too_expensive = expensive + generator.integers(5, 40, size)
    frame = pd.DataFrame(
        {"P_TC": too_cheap, "P_C": cheap, "P_E": expensive, "P_TE": too_expensive}
    )
    # Двое с нарушенным порядком: «дёшево» дороже «дорого».
    frame.loc[0, "P_C"] = frame.loc[0, "P_E"] + 5
    frame.loc[1, "P_TC"] = frame.loc[1, "P_TE"] + 5
    return frame


def _crossing_by_hand(prices, falling, rising):
    for index, price in enumerate(prices):
        gap = falling[index] - rising[index]
        if gap == 0:
            return price
        if index and falling[index - 1] - rising[index - 1] > 0 > gap:
            before = falling[index - 1] - rising[index - 1]
            return prices[index - 1] + before / (before - gap) * (price - prices[index - 1])
    return None


def test_van_westendorp_points_match_a_plain_python_reference(tmp_path: Path) -> None:
    frame = _price_frame()
    project = _project(tmp_path / "psm.sav", frame)
    codes = {"too_cheap": "P_TC", "cheap": "P_C", "expensive": "P_E", "too_expensive": "P_TE"}

    result = van_westendorp(tmp_path / "psm.sav", project, codes)

    assert result["excluded_inconsistent"] == 2
    assert result["consistent_base"] == 78
    rows = [
        tuple(row)
        for row in frame[["P_TC", "P_C", "P_E", "P_TE"]].itertuples(index=False)
        if row[0] <= row[1] <= row[2] <= row[3]
    ]
    prices = sorted({value for row in rows for value in row})
    count = len(rows)

    def at_least(column):
        return [sum(1 for row in rows if row[column] >= price) / count for price in prices]

    def at_most(column):
        return [sum(1 for row in rows if row[column] <= price) / count for price in prices]

    too_cheap, cheap = at_least(0), at_least(1)
    expensive, too_expensive = at_most(2), at_most(3)
    assert result["points"]["optimal"] == pytest.approx(
        _crossing_by_hand(prices, too_cheap, too_expensive)
    )
    assert result["points"]["indifference"] == pytest.approx(
        _crossing_by_hand(prices, cheap, expensive)
    )
    assert result["points"]["marginal_cheapness"] == pytest.approx(
        _crossing_by_hand(prices, too_cheap, [1 - value for value in cheap])
    )
    assert result["points"]["marginal_expensiveness"] == pytest.approx(
        _crossing_by_hand(prices, [1 - value for value in expensive], too_expensive)
    )
    assert (
        result["points"]["marginal_cheapness"]
        <= result["points"]["optimal"]
        <= result["points"]["marginal_expensiveness"]
    )


def test_gabor_granger_demand_and_revenue_by_hand(tmp_path: Path) -> None:
    # 10 ответивших: «да» на 100 — 8, на 150 — 5, на 200 — 2.
    frame = pd.DataFrame(
        {
            "G_100": [1] * 8 + [2] * 2,
            "G_150": [1] * 5 + [2] * 5,
            "G_200": [1] * 2 + [2] * 8,
        }
    )
    project = _project(tmp_path / "gg.sav", frame)
    steps = [
        {"code": "G_150", "price": 150, "buy_values": [1]},
        {"code": "G_100", "price": 100, "buy_values": [1]},
        {"code": "G_200", "price": 200, "buy_values": [1]},
    ]

    result = gabor_granger(tmp_path / "gg.sav", project, steps)

    assert [point["price"] for point in result["points"]] == [100, 150, 200]
    assert [point["demand"] for point in result["points"]] == pytest.approx([0.8, 0.5, 0.2])
    assert [point["revenue"] for point in result["points"]] == pytest.approx([80, 75, 40])
    assert result["optimal_price"] == 100


def test_methods_run_through_the_api(tmp_path: Path) -> None:
    source = tmp_path / "gg.sav"
    pyreadstat.write_sav(
        pd.DataFrame({"LOWPRICE": [1] * 30 + [2] * 10, "HIGHPRICE": [1] * 20 + [2] * 20}),
        source,
        variable_value_labels={
            "LOWPRICE": {1: "Да", 2: "Нет"},
            "HIGHPRICE": {1: "Купил бы", 2: "Не купил бы"},
        },
        variable_measure={"LOWPRICE": "nominal", "HIGHPRICE": "nominal"},
    )
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects", files={"file": ("gg.sav", stream, "application/octet-stream")}
            ).json()["id"]
            result = client.post(
                f"/api/projects/{project_id}/analysis/methods/gabor-granger",
                json={
                    "steps": [
                        {"code": "LOWPRICE", "price": 100, "buy_values": [1]},
                        {"code": "HIGHPRICE", "price": 150, "buy_values": [1]},
                    ]
                },
            )
            assert result.status_code == 200, result.text
            assert result.json()["optimal_price"] == 100

            refused = client.post(
                f"/api/projects/{project_id}/analysis/methods/turf", json={"code": "LOWPRICE"}
            )
            assert refused.status_code == 422
            assert "несколькими ответами" in refused.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def _maxdiff_file(path: Path) -> None:
    """40 одинаковых респондентов, варианты A–D, два задания по три варианта.

    Первое задание показывает A, B, C: лучший A, худший C. Второе — B, C, D:
    лучший B, худший D.
    """
    size = 40
    items = {1: "A", 2: "B", 3: "C", 4: "D"}
    frame = pd.DataFrame(
        {
            "FIRSTBEST": [1] * size, "FIRSTWORST": [3] * size,
            "SECONDBEST": [2] * size, "SECONDWORST": [4] * size,
            "FIRSTSHOWA": [1] * size, "FIRSTSHOWB": [2] * size, "FIRSTSHOWC": [3] * size,
            "SECONDSHOWA": [2] * size, "SECONDSHOWB": [3] * size, "SECONDSHOWC": [4] * size,
        }
    )
    pyreadstat.write_sav(
        frame,
        path,
        variable_value_labels={name: items for name in frame.columns},
        variable_measure={name: "nominal" for name in frame.columns},
    )


def test_maxdiff_counts_match_the_hand_calculation(tmp_path: Path) -> None:
    from sav_analytics.core.research_methods import maxdiff_counts

    source = tmp_path / "maxdiff.sav"
    _maxdiff_file(source)
    inspection = inspect_sav(source).to_dict()
    project = {
        "inspection": inspection,
        "configuration": {
            "questions": inspection["questions"], "recodings": [], "filters": [],
            "report_settings": {},
        },
    }
    tasks = [
        {"best": "FIRSTBEST", "worst": "FIRSTWORST",
         "shown": ["FIRSTSHOWA", "FIRSTSHOWB", "FIRSTSHOWC"]},
        {"best": "SECONDBEST", "worst": "SECONDWORST",
         "shown": ["SECONDSHOWA", "SECONDSHOWB", "SECONDSHOWC"]},
    ]

    exact = maxdiff_counts(source, project, tasks)

    scores = {item["label"]: item["score"] for item in exact["items"]}
    # A: лучший 1 раз из 1 показа; B: 1 из 2; C: худший 1 из 2; D: худший 1 из 1.
    assert scores == pytest.approx({"A": 1.0, "B": 0.5, "C": -0.5, "D": -1.0})
    assert exact["exposures"] == "по переменным показа"
    assert [item["label"] for item in exact["items"]] == ["A", "B", "C", "D"]

    balanced = maxdiff_counts(
        source, project, [{**task, "shown": []} for task in tasks], items_per_task=3
    )
    # Сбалансированный дизайн: 2 задания × 3 варианта / 4 = 1,5 показа на вариант.
    by_label = {item["label"]: item["score"] for item in balanced["items"]}
    assert by_label["A"] == pytest.approx(1 / 1.5)
    assert balanced["exposures"] == "сбалансированный дизайн"
