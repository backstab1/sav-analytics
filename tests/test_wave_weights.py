"""Свой готовый вес у волны (остаток PQ.19)."""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import numpy as np
import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.formulas import read_project_frame
from sav_analytics.core.reporting.data import report_weights
from sav_analytics.core.reporting.live import build_live_table
from sav_analytics.repository import ProjectRepository

SPRING = [0.8, 1.2] * 10
AUTUMN = [5.0, 15.0] * 15


@pytest.fixture
def context(tmp_path: Path) -> Iterator[dict]:
    """Весна — 20 анкет с весом W; осень — 30 анкет, у неё W пуст, а свой вес W2.

    W2 в другом масштабе (среднее 10): общая нормировка отдала бы осени
    лишнюю долю, поэтому вес нормируется внутри каждой волны.
    """
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "waves.sav"
    rows = {
        "WAVE": [1.0] * 20 + [2.0] * 30,
        "YES": [1.0, 0.0] * 10 + [1.0] * 10 + [0.0] * 20,
        "W": SPRING + [np.nan] * 30,
        "W2": [np.nan] * 20 + AUTUMN,
    }
    pyreadstat.write_sav(
        pd.DataFrame(rows),
        source,
        column_labels={"WAVE": "Волна", "YES": "Согласие", "W": "Вес", "W2": "Вес осени"},
        variable_value_labels={
            "WAVE": {1.0: "Весна", 2.0: "Осень"},
            "YES": {0.0: "Нет", 1.0: "Да"},
        },
        variable_measure={"WAVE": "nominal", "YES": "nominal", "W": "scale", "W2": "scale"},
    )
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects", files={"file": ("waves.sav", stream, "application/octet-stream")}
            ).json()["id"]
            base = f"/api/projects/{project_id}"
            for code, role in (("WAVE", "wave"), ("W", "weight"), ("W2", "weight")):
                response = client.patch(f"{base}/questions/{code}", json={"role": role})
                assert response.status_code == 200, response.text
            assert client.put(f"{base}/waves/view", json={"mode": "all"}).status_code == 200
            yield {"client": client, "base": base, "id": UUID(project_id),
                   "repository": repository, "source": source}
    finally:
        app.dependency_overrides.clear()


def _put_settings(context: dict, **changes: object):
    client = context["client"]
    current = client.get(context["base"]).json()["configuration"]["report_settings"]
    return client.put(f"{context['base']}/report-settings", json={**current, **changes})


def test_wave_weighs_with_its_own_variable_normalized_within_the_wave(context) -> None:
    # Без своего веса у осени W не годится: у её анкет он пуст.
    assert _put_settings(context, weight_variable="W").status_code == 422

    response = _put_settings(
        context, weight_variable="W", wave_weights=[{"wave": 2, "variable": "W2"}]
    )
    assert response.status_code == 200, response.text

    repository = context["repository"]
    project = repository.get(context["id"])
    frame = read_project_frame(repository.source_path(context["id"]), project)
    weights, label = report_weights(frame, "W", None, project)

    assert weights is not None
    assert weights[:20].tolist() == pytest.approx([value / 1.0 for value in SPRING])
    assert weights[20:].tolist() == pytest.approx([value / 10.0 for value in AUTUMN])
    assert label == "W; у «Осень» — W2"


def test_book_shares_use_the_wave_weights(context) -> None:
    _put_settings(context, weight_variable="W", wave_weights=[{"wave": 2, "variable": "W2"}])
    repository = context["repository"]
    project = repository.get(context["id"])

    table = build_live_table(repository.source_path(context["id"]), project, questions=["YES"])

    rows = {row["label"]: row for row in table["questions"][0]["rows"]}
    spring = np.array(SPRING) / 1.0
    autumn = np.array(AUTUMN) / 10.0
    yes = spring[0::2].sum() + autumn[:10].sum()
    expected = yes / (spring.sum() + autumn.sum()) * 100
    assert rows["Да"]["cells"][0]["value"] == pytest.approx(expected)
    assert table["settings"]["weight"] == "W; у «Осень» — W2"


def test_one_selected_wave_uses_its_own_weight(context) -> None:
    _put_settings(context, weight_variable="W", wave_weights=[{"wave": 2, "variable": "W2"}])
    client = context["client"]
    assert client.put(f"{context['base']}/waves/view",
                      json={"mode": "wave", "value": 2}).status_code == 200
    repository = context["repository"]
    project = repository.get(context["id"])

    table = build_live_table(repository.source_path(context["id"]), project, questions=["YES"])

    rows = {row["label"]: row for row in table["questions"][0]["rows"]}
    autumn = np.array(AUTUMN)
    assert rows["Да"]["cells"][0]["value"] == pytest.approx(
        autumn[:10].sum() / autumn.sum() * 100
    )


def test_unusable_wave_weight_is_refused_with_the_wave_named(context) -> None:
    # Осень остаётся на общем весе W, а у неё он пуст: ошибка называет волну.
    rest = _put_settings(
        context, weight_variable="W", wave_weights=[{"wave": 1, "variable": "W2"}]
    )
    assert rest.status_code == 422
    assert "Волны «Осень»" in rest.text
    # У весны W2 пуст.
    response = _put_settings(
        context,
        weight_variable="W",
        wave_weights=[{"wave": 1, "variable": "W2"}, {"wave": 2, "variable": "W2"}],
    )
    assert response.status_code == 422
    assert "Волна «Весна»" in response.text

    unknown = _put_settings(
        context, weight_variable="W", wave_weights=[{"wave": 7, "variable": "W2"}]
    )
    assert unknown.status_code == 422
    alone = _put_settings(
        context, weight_variable=None, wave_weights=[{"wave": 2, "variable": "W2"}]
    )
    assert alone.status_code == 422


def test_wave_weight_keeps_its_weight_role(context) -> None:
    _put_settings(context, weight_variable="W", wave_weights=[{"wave": 2, "variable": "W2"}])
    refused = context["client"].patch(f"{context['base']}/questions/W2", json={"role": "question"})
    assert refused.status_code in {409, 422}, refused.text

def test_diagnostics_skip_waves_with_their_own_weight(context) -> None:
    client = context["client"]
    url = f"{context['base']}/weights/ready/W/diagnostics"

    whole = client.get(url).json()
    without_autumn = client.get(url, params={"own_wave": "2"}).json()

    assert whole["usable"] is False
    assert without_autumn["usable"] is True
    assert without_autumn["diagnostics"]["count"] == 20
