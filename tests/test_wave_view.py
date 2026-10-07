"""Выбранная волна и единая переменная волны.

Волны бывают размеченными в данных (переменная с ролью «Волна») и
подгруженными файлами. Оба вида — значения одной переменной: файл без своей
переменной волны получает новый код той же переменной, а не вторую
переменную. Выбранная волна сужает расчёты до своих строк.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.formulas import read_project_frame
from sav_analytics.core.reporting.live import build_live_table
from sav_analytics.repository import ProjectRepository


def _write(path: Path, rows: dict, labels: dict, value_labels: dict) -> None:
    pyreadstat.write_sav(
        pd.DataFrame(rows), path, column_labels=labels, variable_value_labels=value_labels,
        variable_measure={name: "nominal" for name in rows},
    )


@pytest.fixture
def context(tmp_path: Path) -> Iterator[dict]:
    """Массив с волной в данных: весна (2 анкеты) и осень (3 анкеты)."""
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "base.sav"
    _write(
        source,
        {"ID": [1, 2, 3, 4, 5], "WAVE": [1, 1, 2, 2, 2], "SEX": [1, 2, 1, 2, 2]},
        {"ID": "Номер", "WAVE": "Волна опроса", "SEX": "Пол"},
        {"WAVE": {1: "Весна", 2: "Осень"}, "SEX": {1: "М", 2: "Ж"}},
    )
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project_id = client.post(
                "/api/projects", files={"file": ("base.sav", stream, "application/octet-stream")}
            ).json()["id"]
            base = f"/api/projects/{project_id}"
            response = client.patch(f"{base}/questions/WAVE", json={"role": "wave"})
            assert response.status_code == 200, response.text
            yield {"client": client, "base": base, "id": UUID(project_id),
                   "repository": repository, "tmp": tmp_path}
    finally:
        app.dependency_overrides.clear()


def _frame(context: dict, columns: list[str]) -> pd.DataFrame:
    repository = context["repository"]
    return read_project_frame(
        repository.source_path(context["id"]), repository.get(context["id"]), columns
    )


def test_wave_in_data_is_selectable_and_narrows_every_read(context) -> None:
    overview = context["client"].get(f"{context['base']}/waves").json()
    assert overview["variable"] == "WAVE"
    assert [(item["label"], item["count"]) for item in overview["values"]] == [
        ("Весна", 2), ("Осень", 3),
    ]
    # По умолчанию — последняя волна.
    assert overview["view"]["mode"] == "wave" and overview["view"]["label"] == "Осень"
    assert len(_frame(context, ["SEX"])) == 3
    assert "WAVE" not in _frame(context, ["SEX"]).columns

    chosen = context["client"].put(f"{context['base']}/waves/view",
                                   json={"mode": "wave", "value": 1})
    assert chosen.status_code == 200, chosen.text
    assert _frame(context, ["SEX"])["SEX"].tolist() == [1, 2]

    context["client"].put(f"{context['base']}/waves/view", json={"mode": "all"})
    assert len(_frame(context, ["SEX"])) == 5
    # Выбор волны — обычная правка: «Отменить» возвращает прежнюю волну.
    context["client"].post(f"{context['base']}/undo")
    assert len(_frame(context, ["SEX"])) == 2

    refused = context["client"].put(f"{context['base']}/waves/view",
                                    json={"mode": "wave", "value": 7})
    assert refused.status_code == 422


def test_compare_mode_keeps_wave_as_a_column(context) -> None:
    repository = context["repository"]
    project = repository.set_wave_view(context["id"], "compare")
    live = build_live_table(
        repository.source_path(context["id"]), project, questions=["SEX"],
        blocks=[{"label": "Волна", "sources": [{"kind": "question", "ref": "WAVE"}]}],
    )
    assert [column["label"] for column in live["columns"]] == ["Total", "Весна", "Осень"]
    assert [column["base"] for column in live["columns"]] == [5, 2, 3]


def test_file_wave_extends_the_wave_variable_in_data(context) -> None:
    second = context["tmp"] / "winter.sav"
    _write(second, {"ID": [6, 7], "SEX": [1, 1]}, {"ID": "Номер", "SEX": "Пол"},
           {"SEX": {1: "М", 2: "Ж"}})
    client = context["client"]
    with second.open("rb") as stream:
        preview = client.post(f"{context['base']}/waves/stage",
                              files={"file": ("winter.sav", stream, "application/octet-stream")})
    assert preview.status_code == 200, preview.text
    preview = preview.json()
    # Волна в данных сопоставляется как обычная переменная: в этом файле её нет.
    assert {row["target"]: row["source"] for row in preview["mapping"]}["WAVE"] is None
    added = client.post(f"{context['base']}/waves", json={
        "staging_id": preview["staging_id"], "label": "Зима", "mapping": {}, "added": [],
    })
    assert added.status_code == 200, added.text
    project = added.json()
    # Вторая переменная волны не заводится: у «Зимы» новый код той же WAVE.
    assert project["waves_meta"] == {"in_data": True, "variable": "WAVE"}
    assert not any(item["name"].startswith("WAVE_") for item in project["inspection"]["variables"])
    frame, meta = pyreadstat.read_sav(context["repository"].source_path(context["id"]))
    assert frame["WAVE"].tolist() == [1, 1, 2, 2, 2, 3, 3]
    assert meta.variable_value_labels["WAVE"] == {1.0: "Весна", 2.0: "Осень", 3.0: "Зима"}
    overview = client.get(f"{context['base']}/waves").json()
    assert [(item["label"], item["count"]) for item in overview["values"]] == [
        ("Весна", 2), ("Осень", 3), ("Зима", 2),
    ]
    assert [item["label"] for item in overview["waves"]] == ["Исходный файл", "Зима"]


def test_file_wave_with_its_own_wave_codes_keeps_them(context) -> None:
    second = context["tmp"] / "next.sav"
    _write(second, {"ID": [6, 7], "WAVE": [5, 5], "SEX": [2, 2]},
           {"ID": "Номер", "WAVE": "Волна опроса", "SEX": "Пол"},
           {"WAVE": {5: "Лето"}, "SEX": {1: "М", 2: "Ж"}})
    client = context["client"]
    with second.open("rb") as stream:
        preview = client.post(f"{context['base']}/waves/stage",
                              files={"file": ("next.sav", stream, "application/octet-stream")}
                              ).json()
    added = client.post(f"{context['base']}/waves", json={
        "staging_id": preview["staging_id"], "label": "Файл лета", "mapping": {}, "added": [],
    })
    assert added.status_code == 200, added.text
    frame, meta = pyreadstat.read_sav(context["repository"].source_path(context["id"]))
    assert frame["WAVE"].tolist() == [1, 1, 2, 2, 2, 5, 5]
    assert meta.variable_value_labels["WAVE"][5.0] == "Лето"


def test_compare_mode_adds_wave_columns_without_banner_setup(context) -> None:
    repository = context["repository"]
    project = repository.set_wave_view(context["id"], "compare")
    path = repository.source_path(context["id"])
    alone = build_live_table(path, project, questions=["SEX"])
    assert [column["label"] for column in alone["columns"]] == ["Total", "Весна", "Осень"]
    beside = build_live_table(
        path, project, questions=["SEX"],
        blocks=[{"label": "Пол", "sources": [{"kind": "question", "ref": "SEX"}]}],
    )
    # Волна — первым блоком, разрез пользователя — следом.
    assert [column["label"] for column in beside["columns"]] == [
        "Total", "Весна", "Осень", "М", "Ж",
    ]
    # В режиме одной волны колонка волны сама не появляется.
    project = repository.set_wave_view(context["id"], "wave", 2)
    single = build_live_table(path, project, questions=["SEX"])
    assert [column["label"] for column in single["columns"]] == ["Total"]
    assert single["columns"][0]["base"] == 3


def _waves_project(tmp_path: Path) -> tuple[ProjectRepository, UUID]:
    """Весной «Да» у 30%, осенью у 70% — изменение значимо."""
    repository = ProjectRepository(tmp_path / "shadow", max_upload_bytes=10_000_000)
    source = tmp_path / "shadow.sav"
    _write(
        source,
        {"WAVE": [1] * 100 + [2] * 100, "YES": [1] * 30 + [2] * 70 + [1] * 70 + [2] * 30},
        {"WAVE": "Волна", "YES": "Нравится?"},
        {"WAVE": {1: "Весна", 2: "Осень"}, "YES": {1: "Да", 2: "Нет"}},
    )
    with source.open("rb") as stream:
        project = repository.create("Тени", "shadow.sav", stream)
    project_id = UUID(project["id"])
    repository.update_question(project_id, "WAVE", {"role": "wave"})
    stored = repository.get(project_id)
    settings = {**stored["configuration"]["report_settings"], "wave_comparison": "previous"}
    repository.update_report_settings(project_id, settings)
    return repository, project_id


def _yes_row(table: dict) -> list[dict]:
    question = next(item for item in table["questions"] if item["code"] == "YES")
    return next(row for row in question["rows"] if row["label"] == "Да")["cells"]


def test_single_wave_compares_with_previous_like_compare_mode(tmp_path: Path) -> None:
    repository, project_id = _waves_project(tmp_path)
    path = repository.source_path(project_id)

    compare = build_live_table(
        path, repository.set_wave_view(project_id, "compare"), questions=["YES"]
    )
    labels = [column["label"] for column in compare["columns"]]
    autumn_in_compare = _yes_row(compare)[labels.index("Осень")]

    single = build_live_table(
        path, repository.set_wave_view(project_id, "wave", 2), questions=["YES"]
    )
    # Одна волна: колонка одна — Total осени, база 100, а не 200.
    assert [column["label"] for column in single["columns"]] == ["Total"]
    assert single["columns"][0]["base"] == 100
    total = _yes_row(single)[0]
    assert total["value"] == pytest.approx(70)
    # Стрелка та же, что у осени против весны в режиме сравнения.
    assert total["wave"] == autumn_in_compare["wave"] == "higher"

    first = build_live_table(
        path, repository.set_wave_view(project_id, "wave", 1), questions=["YES"]
    )
    # У первой волны предыдущей нет — стрелки нет.
    assert _yes_row(first)[0]["wave"] is None


def test_single_wave_book_and_audit_name_the_compared_wave(tmp_path: Path) -> None:
    from sav_analytics.core.report import build_statistics_txt, build_topline_xlsx

    repository, project_id = _waves_project(tmp_path)
    project = repository.set_wave_view(project_id, "wave", 2)
    path = repository.source_path(project_id)
    assert build_topline_xlsx(path, project)
    audit = build_statistics_txt(path, project)
    # Тест изменения — против той же колонки в предыдущей волне.
    assert "Весна · Total" in audit
