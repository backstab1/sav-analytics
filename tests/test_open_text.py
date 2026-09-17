"""Кодирование открытых ответов (PQ.12)."""

from pathlib import Path

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.open_text import CodeframeError, parse_query, query_matches
from sav_analytics.core.report import build_topline_xlsx
from sav_analytics.core.russian_stemmer import stem
from sav_analytics.repository import ProjectRepository
from tests.test_report import _cell_value, _row_labels

ANSWERS = [
    "Очень доволен доставкой",
    "Доставку привезли быстро",
    "Дорого, но качественно",
    "Цены высокие",
    "",
    "Довольна качеством и ценой",
    "Курьер опоздал с доставкой",
    "Ничего не понравилось",
]


def test_snowball_stems_join_word_forms() -> None:
    assert stem("доставка") == stem("доставкой") == stem("доставку") == "доставк"
    assert stem("красивые") == stem("красивая") == "красив"
    assert stem("ёлки") == "елк"


def test_queries_find_word_forms_prefixes_and_exclusions() -> None:
    texts = pd.Series(ANSWERS)

    delivery = query_matches(texts, ["доставка"])
    assert delivery.tolist() == [True, True, False, False, False, False, True, False]

    # «доволен» и «довольна» — через префикс основы.
    assert query_matches(texts, ["доволен"]).tolist()[5] is True
    # Все слова строки — И, исключение — минусом.
    assert query_matches(texts, ["доставка -курьер"]).tolist()[6] is False
    assert query_matches(texts, ["цен*"]).tolist() == [
        False, False, False, True, False, True, False, False
    ]
    with pytest.raises(CodeframeError):
        parse_query("-курьер")


def _write(path: Path) -> None:
    pyreadstat.write_sav(
        pd.DataFrame({"ID": list(range(1, 9)), "WHY": ANSWERS}),
        path,
        column_labels={"ID": "Номер", "WHY": "Почему вы так оценили?"},
        variable_measure={"ID": "nominal"},
    )


def test_codeframe_becomes_a_multiple_response_question(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "open.sav"
    _write(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                files={"file": ("open.sav", stream, "application/octet-stream")},
            ).json()
            base = f"/api/projects/{project['id']}"
            assert client.patch(
                f"{base}/questions/WHY", json={"question_type": "open_text"}
            ).status_code == 200
            created = client.post(f"{base}/codeframes", json={"question_code": "WHY"})
            assert created.status_code == 201
            codeframe = created.json()["configuration"]["codeframes"][0]
            url = f"{base}/codeframes/{codeframe['id']}"

            updated = client.put(
                url,
                json={
                    "label": "Темы: почему",
                    "themes": [
                        {"id": "new-1", "name": "Доставка", "queries": ["доставка"]},
                        {"id": "new-2", "name": "Цена", "queries": ["цен*", "дорого"]},
                        {"id": "new-3", "name": "Курьер", "parent_id": "new-1",
                         "queries": ["курьер"]},
                    ],
                },
            )
            assert updated.status_code == 200, updated.text
            configuration = updated.json()["configuration"]
            themes = configuration["codeframes"][0]["themes"]
            price = next(theme for theme in themes if theme["name"] == "Цена")
            question = next(
                item for item in configuration["questions"] if item["code"] == codeframe["code"]
            )
            assert question["question_type"] == "multiple_choice_dichotomy"
            assert question["source_variables"] == [f"{codeframe['code']}_{n}" for n in (1, 2, 3)]
            assert (question["valid_count"], question["missing_count"]) == (7, 1)

            summary = client.get(f"{url}/summary").json()
            counts = {item["id"]: item["count"] for item in summary["themes"]}
            assert counts[price["id"]] == 3
            assert summary["answered"] == 7
            assert summary["uncoded"] == 1  # только «Ничего не понравилось»

            # Человек снимает «Цену» с 3-й строки и ставит на 8-ю.
            client.put(f"{url}/marks", json={"theme_id": price["id"], "row": 2, "value": False})
            marked = client.put(
                f"{url}/marks", json={"theme_id": price["id"], "row": 7, "value": True}
            )
            assert marked.status_code == 200
            rows = client.get(f"{url}/answers", params={"theme_id": price["id"]}).json()
            assert [row["row"] for row in rows["rows"]] == [3, 5, 7]
            assert {row["row"]: row["themes"][0]["source"] for row in rows["rows"]}[7] == "manual"

            uncoded = client.get(f"{url}/answers", params={"uncoded": True}).json()
            assert all(row["themes"] == [] for row in uncoded["rows"])

            stored = repository.get(project["id"])
            content = build_topline_xlsx(repository.source_path(project["id"]), stored)
            assert "Доставка" in _row_labels(content)
            # Доставка: строки 0, 1, 6 (курьер — дочерняя тема). Главный лист считает
            # от полной базы — 8 респондентов, включая не ответившего.
            assert _cell_value(content, "Доставка", "B") == pytest.approx(3 / 8 * 100)

            refreshed = client.post(f"{base}/structure/refresh").json()
            assert any(
                item["code"] == codeframe["code"]
                for item in refreshed["configuration"]["questions"]
            )

            deleted = client.delete(url)
            assert deleted.status_code == 200
            assert all(
                item["code"] != codeframe["code"]
                for item in deleted.json()["configuration"]["questions"]
            )
    finally:
        app.dependency_overrides.clear()


def test_text_profile_separates_answers_from_service_fields() -> None:
    from sav_analytics.core.open_text import text_profile

    answers = text_profile(pd.Series(ANSWERS))
    logins = text_profile(pd.Series(["ivanov", "petrov", "2026-09-01 10:22", "", "+79001234567"]))

    assert answers["wordy_share"] > 0.8
    assert logins["wordy_share"] == 0
    assert answers["answered"] == 7

