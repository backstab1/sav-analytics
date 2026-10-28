"""Выгрузка платформы в Excel и SAV вместе с анкетой JSON (имена, подписи, типы)."""

import json
from pathlib import Path

import pandas as pd
import pyreadstat
import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.survey_schema import SurveySchemaError, parse_schema
from sav_analytics.repository import ProjectRepository

LONG_QUESTION = "S5. Как часто вы покупаете продукты в магазинах и заказываете доставку? " * 3


def _answers(name: str, items: list[tuple[int, str]], open_code: int | None = None) -> dict:
    return {
        "Name": name,
        "Type": "Common",
        "AnswerItems": [
            {
                "Code": code,
                "Text": text,
                "ExportedFlags": {"OpenValueTxt": code == open_code},
            }
            for code, text in items
        ],
    }


SCHEMA = {
    "Name": "Тестовая анкета",
    "ColumnCodeTemplateSeparator": "_",
    "Questions": [
        {"Number": 1, "QuestionType": "SingleChoice", "Text": "S1. <b>Вы</b>…",
         "AnswerList": "SEX"},
        {"Number": 2, "QuestionType": "Numeric", "Text": "S2. Сколько вам полных лет?"},
        {"Number": 10, "QuestionType": "Information", "Text": "Спасибо!"},
        {"Number": 5, "QuestionType": "Table_SingleChoice", "Text": LONG_QUESTION,
         "AnswerList": "FREQ", "RowList": "ROWS"},
        {"Number": 6, "QuestionType": "MultipleChoice",
         "Text": "S6. Где вы делали заказ?<br>Выберите все варианты.", "AnswerList": "SVC"},
    ],
    "AnswerLists": [
        _answers("SEX", [(1, "Мужчина"), (2, "Женщина")]),
        _answers("FREQ", [(1, "Каждый день"), (2, "Раз в неделю"), (99, "Не покупаю")]),
        _answers("ROWS", [(1, "В магазинах (офлайн)"), (2, "В доставке (онлайн)")]),
        _answers("SVC", [(1, "Самокат"), (2, "Лавка"), (10, "Другой (указать)")], open_code=10),
    ],
}

HEADERS = [
    "ID. Идентификатор респондента",
    "Q1. S1. Вы…",
    "Q2. S2. Сколько вам полных лет?",
    "Q5_1. В магазинах (офлайн)",
    "Q5_2. В доставке (онлайн)",
    "Q6_1. Самокат",
    "Q6_2. Лавка",
    "Q6_10. Другой (указать)",
    "Q6_10T. Другой (указать)",
    "IVDate1. Дата начала",
]
ROWS = [
    ["a1", 1, 25, 1, 2, 1, 0, 0, None, 46302.5],
    ["a2", 2, 31, 2, 99, 0, 1, 1, "Магнит", 46302.75],
    ["a3", 2, 44, 1, 1, 1, 1, 0, None, 46303.0],
    ["a4", 1, 52, 99, 2, 0, 1, 1, "Спар", 46303.25],
    ["a5", 1, 38, 2, 2, 1, 0, 0, None, 46304.5],
    ["a6", 2, 29, 1, 99, 1, 1, 0, None, 46305.125],
]


def _workbook(path: Path, rows: list[list] = ROWS) -> Path:
    import xlsxwriter

    workbook = xlsxwriter.Workbook(str(path))
    sheet = workbook.add_worksheet("Sheet0")
    stamp = workbook.add_format({"num_format": "yyyy-MM-dd HH:mm:ss"})
    for column, header in enumerate(HEADERS):
        sheet.write(0, column, header)
    for row_index, row in enumerate(rows, start=1):
        for column, value in enumerate(row):
            if value is None:
                continue
            if HEADERS[column].startswith("IVDate1"):
                sheet.write_number(row_index, column, value, stamp)
            else:
                sheet.write(row_index, column, value)
    workbook.close()
    return path


def _schema_file(path: Path) -> Path:
    path.write_text(json.dumps(SCHEMA, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def client(tmp_path: Path):
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as test_client:
            test_client.repository = repository
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _upload(client: TestClient, data: Path, schema: Path | None = None):
    files = {"file": (data.name, data.read_bytes(), "application/octet-stream")}
    if schema is not None:
        files["questionnaire"] = (schema.name, schema.read_bytes(), "application/json")
    return client.post("/api/projects", files=files)


def _by(items: list[dict], key: str) -> dict[str, dict]:
    return {item[key]: item for item in items}


def test_platform_excel_without_questionnaire_keeps_names_and_groups(
    client: TestClient, tmp_path: Path
) -> None:
    response = _upload(client, _workbook(tmp_path / "export.xlsx"))
    assert response.status_code == 201, response.text
    project = response.json()
    variables = _by(project["inspection"]["variables"], "name")
    # Имя — из заголовка «Q6_1. Самокат», подпись — остаток заголовка.
    assert list(variables) == [header.split(".")[0] for header in HEADERS]
    assert variables["Q6_1"]["label"] == "Самокат"
    assert variables["Q1"]["label"] == "S1. Вы…"
    # Дата Excel — текстом, а не числом дней.
    assert variables["IVDate1"]["storage_type"] == "string"
    questions = _by(project["configuration"]["questions"], "code")
    assert questions["Q1"]["question_type"] == "single_choice"
    assert questions["Q2"]["question_type"] == "numeric"
    assert questions["Q5"]["question_type"] == "matrix"
    assert questions["Q5"]["source_variables"] == ["Q5_1", "Q5_2"]
    assert questions["Q6"]["question_type"] == "multiple_choice_dichotomy"
    assert questions["Q6"]["source_variables"] == ["Q6_1", "Q6_2", "Q6_10"]
    assert questions["Q6_10T"]["question_type"] == "open_text"

    frame, _ = pyreadstat.read_sav(client.repository.source_path(project["id"]))
    assert frame["IVDate1"].iloc[0] == "07.10.2026 12:00:00"
    assert frame["Q6_10T"].tolist()[:2] == ["", "Магнит"]


def test_excel_with_questionnaire_gets_labels_codes_and_full_texts(
    client: TestClient, tmp_path: Path
) -> None:
    response = _upload(
        client, _workbook(tmp_path / "export.xlsx"), _schema_file(tmp_path / "qn.json")
    )
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["survey_schema"]["title"] == "Тестовая анкета"
    assert project["survey_schema"]["completed"] == 8
    variables = _by(project["inspection"]["variables"], "name")
    assert [item["label"] for item in variables["Q1"]["value_labels"]] == ["Мужчина", "Женщина"]
    assert [item["label"] for item in variables["Q6_1"]["value_labels"]] == [
        "НЕ ВЫБРАН",
        "ВЫБРАН",
    ]
    assert variables["Q6_1"]["label"] == (
        "S6. Где вы делали заказ? Выберите все варианты. Самокат"
    )
    # Длинный вопрос в SAV не помещается: полный текст живёт в правках подписей.
    full = f"{LONG_QUESTION.strip()} В доставке (онлайн)"
    assert variables["Q5_2"]["label"] == full
    assert project["configuration"]["label_overrides"]["Q5_2"]["label"] == full
    questions = _by(project["configuration"]["questions"], "code")
    assert questions["Q5"]["label"] == LONG_QUESTION.strip()
    assert questions["Q5"]["question_type"] == "matrix"
    assert questions["Q2"]["question_type"] == "numeric"

    # В самом SAV подпись строки матрицы сокращена за счёт вопроса, а не пункта.
    _, meta = pyreadstat.read_sav(
        client.repository.source_path(project["id"]), metadataonly=True
    )
    assert meta.column_names_to_labels["Q5_2"].endswith("… В доставке (онлайн)")
    assert meta.column_names_to_labels["Q5_1"] != meta.column_names_to_labels["Q5_2"]


def test_excel_with_answer_texts_is_coded_by_questionnaire(
    client: TestClient, tmp_path: Path
) -> None:
    labelled = [list(row) for row in ROWS]
    for row in labelled:
        row[1] = {1: "Мужчина", 2: "Женщина"}[row[1]]
    response = _upload(
        client,
        _workbook(tmp_path / "labels.xlsx", labelled),
        _schema_file(tmp_path / "qn.json"),
    )
    assert response.status_code == 201, response.text
    frame, meta = pyreadstat.read_sav(
        client.repository.source_path(response.json()["id"])
    )
    assert frame["Q1"].tolist() == [1, 2, 2, 1, 1, 2]
    assert meta.variable_value_labels["Q1"] == {1: "Мужчина", 2: "Женщина"}


def test_sav_with_missing_metadata_is_completed_and_original_kept(
    client: TestClient, tmp_path: Path
) -> None:
    source = tmp_path / "bare.sav"
    frame = pd.DataFrame({"Q1": [1.0, 2.0, 2.0, 1.0], "Q2": [25.0, 31.0, 44.0, 52.0]})
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"Q1": "", "Q2": "Возраст"},
        missing_ranges={"Q2": [99]},
        variable_measure={"Q1": "nominal", "Q2": "scale"},
    )
    response = _upload(client, source, _schema_file(tmp_path / "qn.json"))
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["survey_schema"]["completed"] == 1
    variables = _by(project["inspection"]["variables"], "name")
    assert variables["Q1"]["label"] == "S1. Вы…"
    assert [item["label"] for item in variables["Q1"]["value_labels"]] == ["Мужчина", "Женщина"]
    # Своя подпись SAV сильнее анкеты, пропуски на месте.
    assert variables["Q2"]["label"] == "Возраст"
    assert variables["Q2"]["missing_ranges"]

    original = client.get(f"/api/projects/{project['id']}/source")
    assert original.status_code == 200
    assert original.content == source.read_bytes()


def test_complete_sav_is_not_rewritten(client: TestClient, tmp_path: Path) -> None:
    source = tmp_path / "full.sav"
    pyreadstat.write_sav(
        pd.DataFrame({"Q1": [1.0, 2.0]}),
        source,
        column_labels={"Q1": "Пол"},
        variable_value_labels={"Q1": {1: "Мужчина", 2: "Женщина"}},
    )
    response = _upload(client, source, _schema_file(tmp_path / "qn.json"))
    assert response.status_code == 201, response.text
    project_dir = client.repository.root / response.json()["id"]
    assert not (project_dir / "original.sav").exists()
    assert (project_dir / "source.sav").read_bytes() == source.read_bytes()


def test_new_excel_wave_reuses_the_questionnaire(client: TestClient, tmp_path: Path) -> None:
    project = _upload(
        client, _workbook(tmp_path / "w1.xlsx"), _schema_file(tmp_path / "qn.json")
    ).json()
    response = client.put(
        f"/api/projects/{project['id']}/source",
        files={"file": ("w2.xlsx", _workbook(tmp_path / "w2.xlsx").read_bytes(), "x")},
    )
    assert response.status_code == 200, response.text
    _, meta = pyreadstat.read_sav(
        client.repository.source_path(project["id"]), metadataonly=True
    )
    assert meta.variable_value_labels["Q1"] == {1: "Мужчина", 2: "Женщина"}


def test_questionnaire_that_is_not_a_survey_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"rows": []}', encoding="utf-8")
    response = _upload(client, _workbook(tmp_path / "export.xlsx"), wrong)
    assert response.status_code == 422
    assert "Questions" in response.json()["detail"]
    text = tmp_path / "qn.txt"
    text.write_text("анкета", encoding="utf-8")
    assert _upload(client, _workbook(tmp_path / "e2.xlsx"), text).status_code == 422
    with pytest.raises(SurveySchemaError):
        parse_schema(b"not json")


def _apply(client: TestClient, project_id: str, schema: Path):
    return client.post(
        f"/api/projects/{project_id}/survey-schema",
        files={"file": (schema.name, schema.read_bytes(), "application/json")},
    )


def test_questionnaire_applied_to_an_existing_excel_project(
    client: TestClient, tmp_path: Path
) -> None:
    project = _upload(client, _workbook(tmp_path / "export.xlsx")).json()
    edited = client.patch(
        f"/api/projects/{project['id']}/questions/Q2", json={"label": "Возраст"}
    )
    assert edited.status_code == 200, edited.text

    response = _apply(client, project["id"], _schema_file(tmp_path / "qn.json"))
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["survey_schema"]["completed"] == 8
    variables = _by(updated["inspection"]["variables"], "name")
    assert [item["label"] for item in variables["Q1"]["value_labels"]] == ["Мужчина", "Женщина"]
    questions = _by(updated["configuration"]["questions"], "code")
    assert questions["Q5"]["label"] == LONG_QUESTION.strip()
    # Правка аналитика сильнее анкеты.
    assert questions["Q2"]["label"] == "Возраст"
    assert (client.repository.root / project["id"] / "survey_schema.json").is_file()
    # Исходник — по-прежнему загруженный Excel.
    original = client.get(f"/api/projects/{project['id']}/source")
    assert original.content == (tmp_path / "export.xlsx").read_bytes()


def test_questionnaire_applied_to_an_existing_bare_sav(
    client: TestClient, tmp_path: Path
) -> None:
    source = tmp_path / "bare.sav"
    pyreadstat.write_sav(pd.DataFrame({"Q1": [1.0, 2.0, 2.0, 1.0]}), source)
    project = _upload(client, source).json()
    response = _apply(client, project["id"], _schema_file(tmp_path / "qn.json"))
    assert response.status_code == 200, response.text
    variables = _by(response.json()["inspection"]["variables"], "name")
    assert variables["Q1"]["label"] == "S1. Вы…"
    assert [item["label"] for item in variables["Q1"]["value_labels"]] == ["Мужчина", "Женщина"]
    assert client.get(f"/api/projects/{project['id']}/source").content == source.read_bytes()
    # Повторная анкета дополняет загруженный SAV, а не уже дополненный.
    again = _apply(client, project["id"], _schema_file(tmp_path / "qn.json"))
    assert again.status_code == 200, again.text
    wrong = tmp_path / "notes.json"
    wrong.write_text("[]", encoding="utf-8")
    assert _apply(client, project["id"], wrong).status_code == 422
