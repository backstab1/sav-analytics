"""Ручная сборка групп вопросов (PQ.9, P1.4).

В массиве нет ни метаданных multiple-response, ни общих префиксов имён,
поэтому SAV-ридер оставляет каждую переменную отдельным вопросом. Аналитик
собирает их сам.
"""

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.repository import ProjectRepository
from tests.test_report import _cell_value

NAN = float("nan")
FIVE = {1: "Плохо", 2: "Скорее плохо", 3: "Средне", 4: "Скорее хорошо", 5: "Хорошо"}


def _write(path: Path) -> None:
    frame = pd.DataFrame(
        {
            # Слоты категориального multiple: коды выбранных марок.
            "FIRST": [1, 2, 1, 3, NAN, 2],
            "SECOND": [2, NAN, 3, NAN, NAN, 1],
            # Дихотомии без общего префикса.
            "TV": [1, 0, 1, 0, 0, 1],
            "WEB": [0, 1, 1, 0, 1, 1],
            # Оценки одной шкалой — матрица.
            "SPEED": [1, 2, 3, 4, 5, 5],
            "PRICE": [5, 4, 3, 2, 1, 1],
            "SEX": [1, 1, 1, 2, 2, 2],
        }
    )
    brands = {1: "Альфа", 2: "Бета", 3: "Гамма"}
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={
            "FIRST": "Марки: первая",
            "SECOND": "Марки: вторая",
            "TV": "Источник: телевидение",
            "WEB": "Источник: интернет",
            "SPEED": "Оценка скорости",
            "PRICE": "Оценка цены",
            "SEX": "Пол",
        },
        variable_value_labels={
            "FIRST": brands,
            "SECOND": brands,
            "TV": {0: "Нет", 1: "Да"},
            "WEB": {0: "Нет", 1: "Да"},
            "SPEED": FIVE,
            "PRICE": FIVE,
            "SEX": {1: "Мужчина", 2: "Женщина"},
        },
        variable_measure={name: "nominal" for name in frame},
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _create(client: TestClient, tmp_path: Path) -> str:
    source = tmp_path / "groups.sav"
    _write(source)
    with source.open("rb") as stream:
        response = client.post(
            "/api/projects",
            files={"file": ("groups.sav", stream, "application/octet-stream")},
        )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _codes(project: dict) -> list[str]:
    return [item["code"] for item in project["configuration"]["questions"]]


def _group(client: TestClient, project_id: str, **body: object):
    return client.post(f"/api/projects/{project_id}/questions/group", json=body)


def test_slots_become_one_categorical_multiple_in_place(
    client: TestClient, tmp_path: Path
) -> None:
    project_id = _create(client, tmp_path)
    before = _codes(client.get(f"/api/projects/{project_id}").json())
    assert {"FIRST", "SECOND"} <= set(before)

    response = _group(
        client,
        project_id,
        codes=["SECOND", "FIRST"],
        question_type="multiple_choice_categorical",
        code="BRANDS",
    )

    assert response.status_code == 200, response.text
    project = response.json()
    codes = _codes(project)
    assert "FIRST" not in codes and "SECOND" not in codes
    # Группа стоит на месте первого из вопросов, слоты — в порядке структуры.
    assert codes.index("BRANDS") == before.index("FIRST")
    group = next(item for item in project["configuration"]["questions"] if item["code"] == "BRANDS")
    assert group["source_variables"] == ["FIRST", "SECOND"]
    assert group["label"] == "Марки"
    assert group["valid_count"] == 5
    assert group["missing_count"] == 1
    assert group["group_source"] == "manual"

    preview = client.get(f"/api/projects/{project_id}/questions/BRANDS/preview").json()
    assert [(row["label"], row["count"]) for row in preview["rows"]] == [
        ("Альфа", 3),
        ("Бета", 3),
        ("Гамма", 2),
    ]


def test_grouped_multiple_reaches_the_workbook(client: TestClient, tmp_path: Path) -> None:
    project_id = _create(client, tmp_path)
    assert _group(
        client, project_id, codes=["TV", "WEB"], question_type="multiple_choice_dichotomy"
    ).status_code == 200

    response = client.post(f"/api/projects/{project_id}/tables/export", json={
        "questions": [_suggested_code(client, project_id, "TV")],
        "sheet": "main",
        "scope": "table",
    })

    assert response.status_code == 200, response.text
    # Телевидение отметили трое из шести.
    assert _cell_value(response.content, "Источник: телевидение", "B") == pytest.approx(50)


def _suggested_code(client: TestClient, project_id: str, member: str) -> str:
    project = client.get(f"/api/projects/{project_id}").json()
    return next(
        item["code"]
        for item in project["configuration"]["questions"]
        if member in item["source_variables"]
    )


def test_code_is_suggested_and_never_collides(client: TestClient, tmp_path: Path) -> None:
    project_id = _create(client, tmp_path)

    first = _group(client, project_id, codes=["SPEED", "PRICE"], question_type="matrix")

    assert first.status_code == 200, first.text
    # Общего префикса нет — код от первой переменной.
    assert "SPEED_grp" in _codes(first.json())
    clash = _group(
        client,
        project_id,
        codes=["TV", "WEB"],
        question_type="multiple_choice_dichotomy",
        code="SEX",
    )
    assert clash.status_code == 422
    assert "занят" in clash.json()["detail"]


def test_referenced_question_is_not_swallowed_by_a_group(
    client: TestClient, tmp_path: Path
) -> None:
    project_id = _create(client, tmp_path)
    banner = client.post(
        f"/api/projects/{project_id}/banners",
        json={
            "name": "Телевидение",
            "blocks": [{"label": "ТВ", "sources": [{"kind": "question", "ref": "TV"}]}],
        },
    )
    assert banner.status_code in {200, 201}, banner.text

    response = _group(
        client, project_id, codes=["TV", "WEB"], question_type="multiple_choice_dichotomy"
    )

    assert response.status_code == 422
    assert "используется" in response.json()["detail"]
    assert "TV" in _codes(client.get(f"/api/projects/{project_id}").json())


@pytest.mark.parametrize(
    ("codes", "question_type", "reason"),
    [
        (["FIRST", "SECOND"], "multiple_choice_dichotomy", "категориальный"),
        (["TV", "SPEED"], "matrix", "одинаковой шкалой"),
        (["TV"], "matrix", "хотя бы два"),
    ],
)
def test_incompatible_variables_are_refused_with_the_reason(
    client: TestClient, tmp_path: Path, codes: list[str], question_type: str, reason: str
) -> None:
    project_id = _create(client, tmp_path)

    response = _group(client, project_id, codes=codes, question_type=question_type)

    assert response.status_code == 422
    assert reason in str(response.json()["detail"])


def test_categorical_slots_must_mean_the_same_codes(tmp_path: Path) -> None:
    from sav_analytics.core.question_groups import QuestionGroupError, build_group

    questions = [
        {"code": name, "role": "question", "source_variables": [name], "included_in_report": True}
        for name in ("A", "B")
    ]
    variables = {
        name: {
            "name": name,
            "storage_type": "numeric",
            "value_labels": [{"value": 1, "label": label}],
        }
        for name, label in (("A", "Да"), ("B", "Нет"))
    }

    with pytest.raises(QuestionGroupError, match="по-разному"):
        build_group(questions, variables, ["A", "B"], "multiple_choice_categorical")


def test_ungroup_returns_single_questions(client: TestClient, tmp_path: Path) -> None:
    project_id = _create(client, tmp_path)
    _group(
        client,
        project_id,
        codes=["FIRST", "SECOND"],
        question_type="multiple_choice_categorical",
        code="BRANDS",
    )

    response = client.post(f"/api/projects/{project_id}/questions/BRANDS/ungroup")

    assert response.status_code == 200, response.text
    codes = _codes(response.json())
    assert "BRANDS" not in codes
    assert codes.index("FIRST") + 1 == codes.index("SECOND")


def test_manual_group_survives_structure_refresh(client: TestClient, tmp_path: Path) -> None:
    project_id = _create(client, tmp_path)
    _group(
        client,
        project_id,
        codes=["FIRST", "SECOND"],
        question_type="multiple_choice_categorical",
        code="BRANDS",
        label="Какие марки выбирали",
    )

    response = client.post(f"/api/projects/{project_id}/structure/refresh")

    assert response.status_code == 200, response.text
    questions = response.json()["configuration"]["questions"]
    codes = [item["code"] for item in questions]
    assert "BRANDS" in codes
    assert "FIRST" not in codes and "SECOND" not in codes
    group = next(item for item in questions if item["code"] == "BRANDS")
    assert group["label"] == "Какие марки выбирали"
