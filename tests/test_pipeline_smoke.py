import hashlib
import re
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pyreadstat
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.repository import ProjectRepository

GOLDEN_DIR = Path(__file__).parent / "golden"


def _write_pipeline_fixture(path: Path) -> None:
    frame = pd.DataFrame(
        {
            "GROUP": [1] * 60 + [2] * 40,
            "OUTCOME": [1] * 42 + [2] * 18 + [1] * 20 + [2] * 20,
            "SCORE": [10 + index % 3 for index in range(60)]
            + [5 + index % 3 for index in range(40)],
        }
    )
    pyreadstat.write_sav(
        frame,
        path,
        file_label="Independent P0 golden pipeline fixture",
        column_labels={"GROUP": "Группа", "OUTCOME": "Результат", "SCORE": "Оценка"},
        variable_value_labels={
            "GROUP": {1: "Первая", 2: "Вторая"},
            "OUTCOME": {1: "Да", 2: "Нет"},
        },
        variable_measure={"GROUP": "nominal", "OUTCOME": "nominal", "SCORE": "scale"},
    )


def _wait_for_report(client: TestClient, project_id: str) -> dict:
    result = client.post(f"/api/projects/{project_id}/reports/prepare").json()
    deadline = time.monotonic() + 10
    while result["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.01)
        result = client.get(
            f"/api/projects/{project_id}/reports/jobs/{result['job_id']}"
        ).json()
    assert result["status"] == "complete"
    return result


def _normalize_audit(content: str) -> str:
    return re.sub(r"^Дата расчёта: .+$", "Дата расчёта: <TIMESTAMP>", content, flags=re.MULTILINE)


def test_sav_to_immutable_artifact_matches_full_golden_snapshot(tmp_path: Path) -> None:
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    source = tmp_path / "golden_pipeline.sav"
    _write_pipeline_fixture(source)
    try:
        with TestClient(app) as client, source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                data={"name": "Golden pipeline"},
                files={"file": ("golden_pipeline.sav", stream, "application/octet-stream")},
            ).json()
            project_id = project["id"]
            excluded_group = client.patch(
                f"/api/projects/{project_id}/questions/GROUP",
                headers={"If-Match": str(project["configuration"]["revision"])},
                json={"included_in_report": False},
            )
            assert excluded_group.status_code == 200
            banner = client.post(
                f"/api/projects/{project_id}/banners",
                headers={
                    "If-Match": str(excluded_group.json()["configuration"]["revision"])
                },
                json={
                    "name": "Golden banner",
                    "blocks": [
                        {
                            "label": "Группа",
                            "sources": [{"kind": "question", "ref": "GROUP"}],
                        }
                    ],
                    "compare_to_total": True,
                    "compare_pairwise": True,
                    "confidence_level": 0.95,
                    "bonferroni": False,
                    "minimum_base": 30,
                },
            )
            assert banner.status_code == 201

            prepared = _wait_for_report(client, project_id)
            workbook = client.get(prepared["downloads"]["topline"])
            statistics = client.get(prepared["downloads"]["statistics"])
            assert workbook.status_code == 200
            assert statistics.status_code == 200
            assert prepared["configuration_revision"] == 3
            assert prepared["artifact_id"] == prepared["cache_key"]
            with ZipFile(BytesIO(workbook.content)) as archive:
                names = set(archive.namelist())
                assert "xl/worksheets/sheet1.xml" in names
                assert "xl/sharedStrings.xml" in names
                assert any(name.startswith("xl/comments") for name in names)

            normalized_audit = _normalize_audit(statistics.text)
            expected_hash = (GOLDEN_DIR / "pipeline_statistics.sha256").read_text(
                encoding="ascii"
            ).strip()
            assert hashlib.sha256(normalized_audit.encode("utf-8")).hexdigest() == expected_hash
            assert "Subgroup/Rest: B — Первая vs Rest(B)" in normalized_audit
            assert "Pairwise: B — Первая vs C — Вторая" in normalized_audit
            assert "Метод: z-test" in normalized_audit
            assert "p-value=0.043530" in normalized_audit
            assert "Метод: Welch t-test" in normalized_audit
            assert "t=29.718071" in normalized_audit
            assert "df=83.152617" in normalized_audit
            assert client.get(prepared["downloads"]["topline"]).content == workbook.content
    finally:
        app.dependency_overrides.clear()
