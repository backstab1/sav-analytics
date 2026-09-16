"""Импорт CSV и TSV: таблица становится проектом, как SAV."""

from pathlib import Path

from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.core.report import build_topline_xlsx
from sav_analytics.repository import ProjectRepository

CSV = (
    "Пол;Age;Оценка;Комментарий\n"
    "Мужчина;25;4,5;Хорошо\n"
    "Женщина;41;3;\n"
    "Женщина;33;5;Быстро\n"
    "Мужчина;58;2;Хорошо\n"
)


def _upload(client: TestClient, path: Path, name: str):
    with path.open("rb") as stream:
        return client.post(
            "/api/projects",
            files={"file": (name, stream, "application/octet-stream")},
        )


def _with_client(tmp_path: Path):
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    return repository


def test_csv_becomes_a_project_with_labelled_categories(tmp_path: Path) -> None:
    repository = _with_client(tmp_path)
    source = tmp_path / "survey.csv"
    source.write_bytes(CSV.encode("utf-8-sig"))
    try:
        with TestClient(app) as client:
            response = _upload(client, source, "survey.csv")
            assert response.status_code == 201
            project = response.json()
            variables = {item["label"]: item for item in project["inspection"]["variables"]}

            # Кириллический заголовок — подпись, имя — допустимое для SPSS.
            assert variables["Пол"]["name"] == "V1"
            assert [item["label"] for item in variables["Пол"]["value_labels"]] == [
                "Мужчина",
                "Женщина",
            ]
            assert variables["Age"]["name"] == "Age"
            assert variables["Age"]["storage_type"] == "numeric"
            assert variables["Оценка"]["storage_type"] == "numeric"
            questions = {item["label"]: item for item in project["configuration"]["questions"]}
            assert questions["Пол"]["question_type"] == "single_choice"

            # Исходник — загруженный CSV, а не собранный из него SAV.
            original = client.get(f"/api/projects/{project['id']}/source")
            assert original.status_code == 200
            assert original.content == source.read_bytes()

            content = build_topline_xlsx(
                repository.source_path(project["id"]), repository.get(project["id"])
            )
            assert content.startswith(b"PK")
    finally:
        app.dependency_overrides.clear()


def test_tsv_in_windows_1251_is_read(tmp_path: Path) -> None:
    _with_client(tmp_path)
    source = tmp_path / "regions.tsv"
    source.write_bytes("Регион\tДоход\nМосква\t120\nКазань\t80\n".encode("cp1251"))
    try:
        with TestClient(app) as client:
            response = _upload(client, source, "regions.tsv")
            assert response.status_code == 201
            labels = {item["label"] for item in response.json()["inspection"]["variables"]}
            assert labels == {"Регион", "Доход"}
    finally:
        app.dependency_overrides.clear()


def test_empty_table_and_unknown_format_are_refused(tmp_path: Path) -> None:
    _with_client(tmp_path)
    header_only = tmp_path / "empty.csv"
    header_only.write_text("Пол;Возраст\n", encoding="utf-8")
    workbook = tmp_path / "data.xlsx"
    workbook.write_bytes(b"PK\x03\x04 not really")
    try:
        with TestClient(app) as client:
            empty = _upload(client, header_only, "empty.csv")
            assert empty.status_code == 422
            assert "нет строк" in empty.json()["detail"]
            unknown = _upload(client, workbook, "data.xlsx")
            assert unknown.status_code == 422
            assert "CSV" in unknown.json()["detail"]
    finally:
        app.dependency_overrides.clear()
