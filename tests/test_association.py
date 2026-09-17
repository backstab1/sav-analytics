"""Карточки связи двух переменных (PQ.10). Эталон — SciPy."""

from pathlib import Path

import numpy as np
import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient
from scipy import stats

from sav_analytics.api import app, get_repository
from sav_analytics.core.association import analyse_cards
from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.repository import ProjectRepository


def _write(path: Path) -> None:
    generator = np.random.default_rng(11)
    size = 240
    region = np.tile([1, 2, 3], size // 3)
    sex = np.tile([1, 2], size // 2)
    score = generator.normal(6, 2, size) + (region == 3) * 1.2
    income = score * 3 + generator.normal(0, 4, size)
    rare = np.array([1] * 6 + [2] * (size - 6))
    buyer = np.where(np.arange(size) < 3, 1, 2)
    pyreadstat.write_sav(
        pd.DataFrame(
            {
                "SEX": sex,
                "REGION": region,
                "SCORE": np.round(score, 2),
                "INCOME": np.round(income, 2),
                "RARE": rare,
                "BUYER": buyer,
            }
        ),
        path,
        column_labels={
            "SEX": "Пол",
            "REGION": "Регион",
            "SCORE": "Оценка",
            "INCOME": "Доход",
            "RARE": "Редкий признак",
            "BUYER": "Покупатель",
        },
        variable_value_labels={
            "SEX": {1: "Мужчина", 2: "Женщина"},
            "REGION": {1: "Север", 2: "Центр", 3: "Юг"},
            "RARE": {1: "Да", 2: "Нет"},
            "BUYER": {1: "Да", 2: "Нет"},
        },
        variable_measure={
            "SEX": "nominal",
            "REGION": "nominal",
            "SCORE": "scale",
            "INCOME": "scale",
            "RARE": "nominal",
            "BUYER": "nominal",
        },
    )


def _project(path: Path) -> tuple[dict, pd.DataFrame]:
    _write(path)
    inspection = inspect_sav(path).to_dict()
    types = {
        "SEX": "single_choice",
        "REGION": "single_choice",
        "SCORE": "numeric",
        "INCOME": "numeric",
        "RARE": "single_choice",
        "BUYER": "single_choice",
    }
    for question in inspection["questions"]:
        question["question_type"] = types[question["code"]]
    frame, _ = pyreadstat.read_sav(path)
    return {
        "inspection": inspection,
        "configuration": {"questions": inspection["questions"], "recodings": [], "filters": []},
    }, frame


def _card(a: str, b: str) -> dict:
    return {"a": {"kind": "question", "ref": a}, "b": {"kind": "question", "ref": b}}


def test_each_pair_type_matches_its_scipy_reference(tmp_path: Path) -> None:
    source = tmp_path / "survey.sav"
    project, frame = _project(source)
    cards = [
        _card("SEX", "REGION"),
        _card("REGION", "SCORE"),
        _card("SEX", "SCORE"),
        _card("SCORE", "INCOME"),
    ]

    chi, means3, means2, correlation = analyse_cards(source, project, cards)

    table = pd.crosstab(frame["SEX"], frame["REGION"]).to_numpy()
    statistic, p_value, _, _ = stats.chi2_contingency(table, correction=False)
    assert chi["method"] == "Хи-квадрат Пирсона"
    assert chi["p_value"] == pytest.approx(p_value)
    assert chi["effect"] == pytest.approx(np.sqrt(statistic / (table.sum() * 1)))

    groups = [frame.loc[frame["REGION"] == code, "SCORE"] for code in (1, 2, 3)]
    assert means3["method"] == "Welch ANOVA"
    assert means3["p_value"] == pytest.approx(stats.f_oneway(*groups, equal_var=False).pvalue)
    assert means3["groups"][2]["label"] == "Юг"

    men = frame.loc[frame["SEX"] == 1, "SCORE"]
    women = frame.loc[frame["SEX"] == 2, "SCORE"]
    assert means2["method"] == "Welch t-test"
    assert means2["p_value"] == pytest.approx(stats.ttest_ind(men, women, equal_var=False).pvalue)

    assert correlation["method"] == "Корреляция Пирсона"
    reference = stats.pearsonr(frame["SCORE"], frame["INCOME"])
    assert correlation["effect"] == pytest.approx(reference.statistic)
    assert correlation["p_value"] == pytest.approx(reference.pvalue)
    assert "Чем выше" in correlation["conclusion"]

    # Поправка Benjamini–Hochberg по всем четырём карточкам.
    adjusted = stats.false_discovery_control(
        [chi["p_value"], means3["p_value"], means2["p_value"], correlation["p_value"]],
        method="bh",
    )
    assert [chi["p_adjusted"], means3["p_adjusted"], means2["p_adjusted"],
            correlation["p_adjusted"]] == pytest.approx(list(adjusted))


def test_small_expected_frequencies_switch_to_fisher_exact(tmp_path: Path) -> None:
    source = tmp_path / "survey.sav"
    project, frame = _project(source)

    (card,) = analyse_cards(source, project, [_card("RARE", "BUYER")])

    table = pd.crosstab(frame["RARE"], frame["BUYER"]).to_numpy()
    assert card["method"] == "Точный тест Фишера"
    assert card["p_value"] == pytest.approx(stats.fisher_exact(table)[1])


def test_outliers_switch_to_spearman(tmp_path: Path) -> None:
    source = tmp_path / "survey.sav"
    project, frame = _project(source)
    frame.loc[:9, "INCOME"] = 10_000.0
    pyreadstat.write_sav(frame, source)

    (card,) = analyse_cards(source, project, [_card("SCORE", "INCOME")])

    assert card["method"] == "Корреляция Спирмена"
    reference = stats.spearmanr(frame["SCORE"], frame["INCOME"])
    assert card["effect"] == pytest.approx(reference.statistic)


def test_cards_are_stored_and_block_deleting_what_they_use(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "survey.sav"
    _write(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                files={"file": ("survey.sav", stream, "application/octet-stream")},
            ).json()
            base = f"/api/projects/{project['id']}"
            formula = client.post(
                f"{base}/formulas",
                json={"name": "SCORE2", "label": "Оценка ×2", "expression": "SCORE * 2"},
            ).json()["configuration"]["formulas"][0]
            added = client.post(
                f"{base}/analysis/cards",
                json={
                    "a": {"kind": "question", "ref": "SCORE2"},
                    "b": {"kind": "question", "ref": "SCORE"},
                },
            )
            assert added.status_code == 201
            card_id = added.json()["configuration"]["analysis_cards"][0]["id"]

            listed = client.get(f"{base}/analysis/cards").json()["cards"]
            assert listed[0]["effect"] == pytest.approx(1.0)

            blocked = client.delete(f"{base}/formulas/{formula['id']}")
            assert blocked.status_code == 422
            assert "Анализа" in blocked.json()["detail"]

            same = client.post(
                f"{base}/analysis/cards",
                json={
                    "a": {"kind": "question", "ref": "SCORE"},
                    "b": {"kind": "question", "ref": "SCORE"},
                },
            )
            assert same.status_code == 422

            assert client.delete(f"{base}/analysis/cards/{card_id}").status_code == 200
            assert client.delete(f"{base}/formulas/{formula['id']}").status_code == 200
    finally:
        app.dependency_overrides.clear()
