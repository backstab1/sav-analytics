"""Ranking: transparent permutations, both encodings, API and shared report path."""

import copy
from pathlib import Path

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.preflight import run_preflight
from sav_analytics.core.ranking import RankingError, ranking_items
from sav_analytics.core.report import build_statistics_txt, build_topline_xlsx
from sav_analytics.core.reporting.live import build_live_table
from sav_analytics.core.sav_reader import inspect_sav
from sav_analytics.core.topline import calculate_preview
from sav_analytics.repository import ProjectRepository
from tests.test_live_table import _column_letter, _numeric_cells

NAN = float("nan")
RANKS = [[1, 2, 3], [2, 1, 3], [3, 2, 1], [1, 3, 2], [NAN, NAN, NAN]]
SOURCES = ["ALPHA", "BETA", "GAMMA"]
NAMES = ["Альфа", "Бета", "Гамма"]


def write_ranking(path: Path, encoding: str = "rank_per_item") -> None:
    rows = (
        RANKS
        if encoding == "rank_per_item"
        else [[10 * (ranks.index(rank) + 1) for rank in range(1, 4)] for ranks in RANKS[:4]]
        + [[NAN, NAN, NAN]]
    )
    frame = pd.DataFrame(rows, columns=SOURCES)
    frame["SEX"] = [1, 1, 2, 2, 2]
    frame["weight"] = [1, 2, 1, 2, 1]
    objects = {10: "Альфа", 20: "Бета", 30: "Гамма"}
    pyreadstat.write_sav(
        frame,
        path,
        column_labels=dict(zip(SOURCES, NAMES, strict=True)),
        variable_value_labels={
            **({name: objects for name in SOURCES} if encoding == "item_per_rank" else {}),
            "SEX": {1: "М", 2: "Ж"},
        },
    )


def project_for(path: Path, encoding: str, *, weighted: bool = False) -> dict:
    write_ranking(path, encoding)
    inspection = inspect_sav(path).to_dict()
    question = {
        "code": "R",
        "label": "Предпочтения",
        "question_type": "ranking",
        "ranking_encoding": encoding,
        "source_variables": SOURCES,
        "role": "question",
        "included_in_report": True,
        "valid_count": 4,
        "missing_count": 1,
    }
    others = [
        dict(q, included_in_report=False)
        for q in inspection["questions"]
        if q["code"] in {"SEX", "weight"}
    ]
    return {
        "name": "Ranking",
        "inspection": inspection,
        "configuration": {
            "questions": [question, *others],
            "recodings": [],
            "filters": [],
            "banners": [],
            "report_filter_id": None,
            "report_settings": {
                "minimum_base": 2,
                "weight_variable": "weight" if weighted else None,
                "compare_to_total": True,
                "compare_pairwise": True,
                "bonferroni": True,
                "show_p_values": True,
            },
        },
    }


@pytest.mark.parametrize("encoding", ["rank_per_item", "item_per_rank"])
@pytest.mark.parametrize("weighted", [False, True])
def test_ranking_reference_and_every_workbook_cell(tmp_path: Path, encoding: str, weighted: bool):
    source = tmp_path / "ranks.sav"
    project = project_for(source, encoding, weighted=weighted)
    q = project["configuration"]["questions"][0]
    preview = calculate_preview(source, q, project["inspection"]["variables"], project)
    assert preview["valid_base"] == 4
    assert [item["statistics"]["mean"] for item in preview["items"]] == [1.75, 2, 2.25]
    assert [r["count"] for r in preview["items"][0]["rows"]] == [2, 1, 1]
    assert preview["items"][0]["rows"][0]["percent_main"] == 0.4
    assert preview["items"][0]["rows"][0]["percent_filter"] == 0.5

    table = build_live_table(source, project, questions=["R"])
    rows = table["questions"][0]["rows"]
    means = [r["cells"][0]["value"] for r in rows if r["label"] == "Средний ранг"]
    # Independent scalar arithmetic, no rank conversion or production aggregation.
    weights = [1, 2, 1, 2] if weighted else [1, 1, 1, 1]
    expected = [
        sum(row[j] * w for row, w in zip(RANKS[:4], weights, strict=True)) / sum(weights)
        for j in range(3)
    ]
    assert means == pytest.approx(expected)
    first = next(r for r in rows if r["label"] == "Место 1")
    assert first["cells"][0]["value"] == pytest.approx(300 / 7 if weighted else 40)
    valid = build_live_table(source, project, questions=["R"], sheet="filter")
    first_valid = next(r for r in valid["questions"][0]["rows"] if r["label"] == "Место 1")
    assert first_valid["cells"][0]["value"] == 50
    workbook = _numeric_cells(build_topline_xlsx(source, project))
    for row in rows:
        for i, cell in enumerate(row["cells"], start=2):
            if cell["value"] is not None:
                assert cell["value"] == pytest.approx(
                    workbook[f"{_column_letter(i)}{row['sheet_row']}"]
                )


@pytest.mark.parametrize("bad", [[1, 1, 3], [1, NAN, 3], [0, 2, 3], [1.5, 2, 3], [1, 2, 4]])
def test_bad_rank_is_never_silently_dropped(bad):
    frame = pd.DataFrame([bad, [NAN] * 3], columns=SOURCES)
    question = {"code": "R", "source_variables": SOURCES, "ranking_encoding": "rank_per_item"}
    variables = {name: {"storage_type": "numeric"} for name in SOURCES}
    with pytest.raises(RankingError, match="некорректных ответов — 1"):
        ranking_items(frame, question, variables)


@pytest.mark.parametrize("encoding", ["rank_per_item", "item_per_rank"])
def test_api_group_roundtrip_and_atomic_rejection(tmp_path: Path, encoding: str):
    source = tmp_path / "ranks.sav"
    write_ranking(source, encoding)
    repo = ProjectRepository(tmp_path / "projects", 10_000_000)
    app.dependency_overrides[get_repository] = lambda: repo
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post("/api/projects", files={"file": ("ranks.sav", stream)}).json()
            base = f"/api/projects/{project['id']}"
            rejected = client.post(
                base + "/questions/group",
                json={
                    "codes": SOURCES,
                    "question_type": "ranking",
                    "code": "R",
                },
            )
            assert rejected.status_code == 422
            assert client.get(base).json() == project
            response = client.post(
                base + "/questions/group",
                json={
                    "codes": list(reversed(SOURCES)),
                    "question_type": "ranking",
                    "code": "R",
                    "ranking_encoding": encoding,
                },
            )
            assert response.status_code == 200, response.text
            group = response.json()["configuration"]["questions"][0]
            assert group["source_variables"] == SOURCES
            assert (group["valid_count"], group["missing_count"]) == (4, 1)
            assert client.get(base + "/questions/R/preview").status_code == 200
            patched = client.patch(base + "/questions/R", json={"label": "Новый заголовок"})
            assert patched.status_code == 200, patched.text
            refreshed = repo.refresh_structure(project["id"])
            assert refreshed["configuration"]["questions"][0]["ranking_encoding"] == encoding
            split = client.post(base + "/questions/R/ungroup")
            assert split.status_code == 200, split.text
            assert set(SOURCES) <= {q["code"] for q in split.json()["configuration"]["questions"]}
    finally:
        app.dependency_overrides.clear()


def test_bad_stored_ranking_blocks_preflight(tmp_path: Path):
    source = tmp_path / "ranks.sav"
    project = project_for(source, "rank_per_item")
    project["configuration"]["questions"][0]["ranking_encoding"] = "item_per_rank"
    report = run_preflight(source, project)
    assert not report.can_prepare
    assert "подписи ровно 3" in report.errors[0].message


def test_invalid_group_data_does_not_mutate_project(tmp_path: Path):
    source = tmp_path / "invalid.sav"
    pyreadstat.write_sav(pd.DataFrame([[1, 1, 3], [1, 2, 3]], columns=SOURCES), source)
    repo = ProjectRepository(tmp_path / "projects", 10_000_000)
    with source.open("rb") as stream:
        project = repo.create("Invalid", "invalid.sav", stream)
    from sav_analytics.repository import InvalidUploadError

    with pytest.raises(InvalidUploadError, match="некорректных ответов — 1"):
        repo.group_questions(project["id"], SOURCES, "ranking", ranking_encoding="rank_per_item")
    assert repo.get(project["id"]) == project


def test_audit_and_live_protocol_agree_for_small_subgroups(tmp_path: Path):
    source = tmp_path / "ranks.sav"
    project = project_for(source, "rank_per_item")
    blocks = [{"sources": [{"kind": "question", "ref": "SEX"}]}]
    project["configuration"]["banners"] = [{"id": "sex", "name": "Пол", "blocks": blocks}]
    table = build_live_table(source, project, questions=["R"], blocks=blocks)
    audit = build_statistics_txt(source, project)
    protocols = [
        c["protocol"]
        for row in table["questions"][0]["rows"]
        for c in row["cells"]
        if c.get("protocol")
    ]
    assert protocols
    assert "Welch" in audit
    assert "z-test" in audit
    assert all("ALPHA" not in p for p in protocols)  # labels, not respondent records
    assert "Предпочтения — Альфа" in audit
    audit_lines = {line.strip() for line in audit.splitlines()}
    for protocol in protocols:
        for line in protocol.splitlines():
            if line.startswith(("Метод:", "Базы:", "p-value=")):
                assert line in audit_lines
    before = copy.deepcopy(project)
    build_live_table(source, project, questions=["R"])
    assert project == before


@pytest.mark.parametrize("bad", [[10, 10, 30], [10, NAN, 30], [10, 20, 40]])
def test_positional_ranking_rejects_duplicates_missing_and_unknown(bad):
    variables = {
        name: {
            "storage_type": "numeric",
            "value_labels": [
                {"value": value, "label": label}
                for value, label in zip([10, 20, 30], NAMES, strict=True)
            ],
        }
        for name in SOURCES
    }
    question = {"code": "R", "source_variables": SOURCES, "ranking_encoding": "item_per_rank"}
    with pytest.raises(RankingError, match="некорректных ответов — 1"):
        ranking_items(pd.DataFrame([bad], columns=SOURCES), question, variables)
    variables["BETA"]["value_labels"][0]["label"] = "Другое"
    with pytest.raises(RankingError, match="по-разному"):
        ranking_items(pd.DataFrame([[10, 20, 30]], columns=SOURCES), question, variables)


def test_spss_missing_labels_are_not_ranked_objects(tmp_path: Path):
    path = tmp_path / "missing.sav"
    labels = {10: "Альфа", 20: "Бета", 30: "Гамма", 99: "Пропуск"}
    pyreadstat.write_sav(
        pd.DataFrame([[10, 20, 30], [99, 99, 99]], columns=SOURCES),
        path,
        variable_value_labels={name: labels for name in SOURCES},
        missing_ranges={name: [99] for name in SOURCES},
    )
    inspection = inspect_sav(path).to_dict()
    q = {
        "code": "R",
        "label": "Rank",
        "question_type": "ranking",
        "source_variables": SOURCES,
        "ranking_encoding": "item_per_rank",
    }
    preview = calculate_preview(path, q, inspection["variables"])
    assert preview["valid_base"] == 1
    assert len(preview["items"]) == 3
    assert preview["items"][0]["statistics"]["mean"] == 1


def test_output_selection_and_empty_filtered_base(tmp_path: Path):
    path = tmp_path / "ranks.sav"
    project = project_for(path, "rank_per_item")
    project["configuration"]["report_settings"]["ranking_metrics"] = ["mean"]
    table = build_live_table(path, project, questions=["R"])
    rows = table["questions"][0]["rows"]
    assert not any(row["label"].startswith("Место") for row in rows)
    assert sum(row["label"] == "Средний ранг" for row in rows) == 3
    project["configuration"]["filters"] = [
        {
            "id": "women",
            "name": "Женщины",
            "rule": {
                "kind": "group",
                "operator": "and",
                "items": [
                    {
                        "kind": "condition",
                        "source": {"kind": "question", "ref": "SEX"},
                        "operator": "in",
                        "values": [2],
                    }
                ],
            },
        }
    ]
    project["configuration"]["report_filter_id"] = "women"
    filtered = build_live_table(path, project, questions=["R"], filter_id="women")
    means = [
        r["cells"][0]["value"]
        for r in filtered["questions"][0]["rows"]
        if r["label"] == "Средний ранг"
    ]
    assert means == [2, 2.5, 1.5]
    # Empty ranking fixture still has a nonempty overall sample.
    pyreadstat.write_sav(
        pd.DataFrame(
            {
                **{name: [NAN, NAN] for name in SOURCES},
                "SEX": [1, 2],
                "weight": [1, 1],
            }
        ),
        path,
    )
    report = run_preflight(path, project)
    assert not report.can_prepare
    assert report.errors[0].code == "EMPTY_QUESTION_BASE"
