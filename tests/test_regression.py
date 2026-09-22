"""Модели «Анализа» (PQ.11). Эталон — statsmodels, независимая реализация.

Взвешенные ошибки считаются сэндвичем с квадратами весов, как
`survey::svyglm`; у statsmodels такой оценки нет, поэтому она проверяется
тождеством: при единичных весах сэндвич — это HC0, умноженная на n/(n−1).
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from sav_analytics.core.regression import (
    _linear,
    _logistic,
    fit_model,
    relative_importance,
)
from tests.test_association import _project


def _question(ref: str) -> dict:
    return {"kind": "question", "ref": ref}


def _design(frame: pd.DataFrame) -> np.ndarray:
    return np.column_stack(
        [
            np.ones(len(frame)),
            frame["SCORE"],
            (frame["REGION"] == 2).astype(float),
            (frame["REGION"] == 3).astype(float),
        ]
    )


def test_linear_model_matches_statsmodels_ols(tmp_path: Path) -> None:
    project, frame = _project(tmp_path / "survey.sav")
    definition = {
        "kind": "linear",
        "dependent": _question("INCOME"),
        "predictors": [_question("SCORE"), _question("REGION")],
    }

    result = fit_model(definition, project, frame)

    reference = sm.OLS(frame["INCOME"].to_numpy(), _design(frame)).fit()
    assert result["performed"]
    assert [row["estimate"] for row in result["coefficients"]] == pytest.approx(
        list(reference.params)
    )
    assert [row["std_error"] for row in result["coefficients"]] == pytest.approx(
        list(reference.bse)
    )
    assert [row["p_value"] for row in result["coefficients"]] == pytest.approx(
        list(reference.pvalues)
    )
    assert result["r_squared"] == pytest.approx(reference.rsquared)
    assert result["adjusted_r_squared"] == pytest.approx(reference.rsquared_adj)
    assert result["f_statistic"] == pytest.approx(reference.fvalue)
    assert result["references"] == {"REGION Регион": "Север"}
    assert result["coefficients"][2]["name"] == "REGION Регион: Центр"


def test_weighted_linear_model_matches_wls_coefficients(tmp_path: Path) -> None:
    project, frame = _project(tmp_path / "survey.sav")
    weights = pd.Series(np.where(frame["SEX"] == 1, 1.6, 0.4), index=frame.index)
    definition = {
        "kind": "linear",
        "dependent": _question("INCOME"),
        "predictors": [_question("SCORE"), _question("REGION")],
    }

    result = fit_model(definition, project, frame, weights=weights)

    reference = sm.WLS(frame["INCOME"].to_numpy(), _design(frame), weights=weights).fit()
    assert [row["estimate"] for row in result["coefficients"]] == pytest.approx(
        list(reference.params)
    )
    assert result["r_squared"] == pytest.approx(reference.rsquared)
    assert result["effective_base"] < result["base"]
    assert "сэндвич" in result["method"]


def test_sandwich_with_unit_weights_is_hc0_times_n_over_n_minus_one(tmp_path: Path) -> None:
    _, frame = _project(tmp_path / "survey.sav")
    x = _design(frame)
    y = frame["INCOME"].to_numpy()
    n = len(y)

    fitted = _linear(x, y, np.ones(n), weighted=True)

    reference = sm.OLS(y, x).fit(cov_type="HC0")
    assert [row["std_error"] for row in fitted["coefficients"]] == pytest.approx(
        list(reference.bse * np.sqrt(n / (n - 1)))
    )


def test_logistic_model_matches_statsmodels_logit(tmp_path: Path) -> None:
    project, frame = _project(tmp_path / "survey.sav")
    definition = {
        "kind": "logistic",
        "dependent": _question("SEX"),
        "event": "Женщина",
        "predictors": [_question("SCORE"), _question("INCOME")],
    }

    result = fit_model(definition, project, frame)

    x = np.column_stack([np.ones(len(frame)), frame["SCORE"], frame["INCOME"]])
    reference = sm.Logit((frame["SEX"] == 2).astype(float).to_numpy(), x).fit(disp=0)
    assert result["event"] == "Женщина"
    assert [row["estimate"] for row in result["coefficients"]] == pytest.approx(
        list(reference.params), rel=1e-6
    )
    assert [row["std_error"] for row in result["coefficients"]] == pytest.approx(
        list(reference.bse), rel=1e-6
    )
    assert result["pseudo_r_squared"] == pytest.approx(reference.prsquared, rel=1e-6)
    assert result["coefficients"][1]["odds_ratio"] == pytest.approx(np.exp(reference.params[1]))


def test_weighted_logistic_matches_glm_and_unit_sandwich_is_hc0(tmp_path: Path) -> None:
    _, frame = _project(tmp_path / "survey.sav")
    x = np.column_stack([np.ones(len(frame)), frame["SCORE"], frame["INCOME"]])
    y = (frame["SEX"] == 2).astype(float).to_numpy()
    weights = np.where(frame["REGION"] == 3, 1.8, 0.6)
    weights = weights / weights.mean()

    fitted = _logistic(x, y, weights, weighted=True)
    reference = sm.GLM(y, x, family=sm.families.Binomial(), freq_weights=weights).fit()
    assert [row["estimate"] for row in fitted["coefficients"]] == pytest.approx(
        list(reference.params), rel=1e-6
    )

    n = len(y)
    unit = _logistic(x, y, np.ones(n), weighted=True)
    robust = sm.Logit(y, x).fit(disp=0, cov_type="HC0")
    assert [row["std_error"] for row in unit["coefficients"]] == pytest.approx(
        list(robust.bse * np.sqrt(n / (n - 1))), rel=1e-6
    )


def test_relative_weights_sum_to_r_squared_and_equal_r2_when_orthogonal() -> None:
    generator = np.random.default_rng(3)
    x = generator.normal(size=(400, 3))
    x[:, 1] += 0.7 * x[:, 0]
    y = x @ np.array([0.5, 0.3, 0.2]) + generator.normal(size=400)

    weights = relative_importance(x, y, np.ones(400), ["a", "b", "c"])

    total = sm.OLS(y, sm.add_constant(x)).fit().rsquared
    assert sum(item["weight"] for item in weights) == pytest.approx(total)
    assert sum(item["share"] for item in weights) == pytest.approx(1)

    # Ортогональны после центрирования: корреляция считается от средних.
    raw = generator.normal(size=(400, 2))
    orthogonal = np.linalg.qr(raw - raw.mean(axis=0))[0] * 20
    target = orthogonal @ np.array([1.0, 2.0]) + generator.normal(size=400)
    by_hand = [np.corrcoef(orthogonal[:, index], target)[0, 1] ** 2 for index in range(2)]
    result = relative_importance(orthogonal, target, np.ones(400), ["p", "q"])
    assert [item["weight"] for item in result] == pytest.approx(by_hand, rel=1e-6)


def test_small_base_does_not_build_a_model(tmp_path: Path) -> None:
    project, frame = _project(tmp_path / "survey.sav")
    mask = pd.Series(False, index=frame.index)
    mask.iloc[:25] = True
    definition = {
        "kind": "linear",
        "dependent": _question("INCOME"),
        "predictors": [_question("SCORE"), _question("REGION")],
    }

    result = fit_model(definition, project, frame, mask)

    assert not result["performed"]
    assert "не меньше 30" in result["reason"]


def test_missing_policy_is_explicit_with_bases_before_and_after(tmp_path: Path) -> None:
    project, frame = _project(tmp_path / "survey.sav")
    frame = frame.copy()
    frame.loc[frame.index[:40], "REGION"] = np.nan
    definition = {
        "kind": "linear",
        "dependent": _question("INCOME"),
        "predictors": [_question("SCORE"), _question("REGION")],
    }

    listwise = fit_model(definition, project, frame)
    kept = fit_model({**definition, "missing": "missing_category"}, project, frame)

    assert (listwise["base_before"], listwise["base"]) == (240, 200)
    assert kept["base"] == 240
    assert any(row["name"].endswith("Нет ответа") for row in kept["coefficients"])


def test_model_is_saved_computed_and_protects_its_sources(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from sav_analytics.api import app, get_repository
    from sav_analytics.repository import ProjectRepository
    from tests.test_association import _write

    source = tmp_path / "survey.sav"
    _write(source)
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects", files={"file": ("survey.sav", stream, "application/octet-stream")}
            ).json()["id"]
            recoding = client.post(
                f"/api/projects/{project_id}/recodings",
                json={
                    "code": "SCORE_HIGH",
                    "name": "Высокая оценка",
                    "source_variable": "SCORE",
                    "categories": [
                        {"label": "До 6", "lower": -100, "upper": 6},
                        {"label": "Выше 6", "lower": 6.0001, "upper": 100},
                    ],
                },
            ).json()["configuration"]["recodings"][0]
            refused = client.post(
                f"/api/projects/{project_id}/analysis/models",
                json={
                    "dependent": _question("INCOME"),
                    "predictors": [_question("INCOME")],
                },
            )
            assert refused.status_code == 422

            created = client.post(
                f"/api/projects/{project_id}/analysis/models",
                json={
                    "kind": "logistic",
                    "dependent": {"kind": "recoding", "ref": recoding["id"]},
                    "event": "Выше 6",
                    "predictors": [_question("INCOME")],
                },
            )
            assert created.status_code == 201, created.text

            (model,) = client.get(f"/api/projects/{project_id}/analysis/models").json()["models"]
            assert model["performed"] and model["event"] == "Выше 6"
            assert model["coefficients"][1]["odds_ratio"] > 1

            blocked = client.delete(f"/api/projects/{project_id}/recodings/{recoding['id']}")
            assert blocked.status_code == 422
            assert "модель" in blocked.json()["detail"]

            removed = client.delete(f"/api/projects/{project_id}/analysis/models/{model['id']}")
            assert removed.status_code == 200
    finally:
        app.dependency_overrides.clear()
