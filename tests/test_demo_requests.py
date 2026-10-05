"""Заявки на демо с лендинга: журнал, проверка полей, ловушка для ботов и почта."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app
from sav_analytics.api_dependencies import get_settings
from sav_analytics.routers import demo_requests
from sav_analytics.settings import Settings

VALID = {
    "name": "Анна Иванова",
    "email": "anna@example.com",
    "phone": "+7 (900) 000-00-00",
    "company": "Исследовательское агентство",
    "role": "Руководитель исследований",
    "message": "Трекинг бренда и веса",
    "consent": True,
}


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    current = Settings(data_dir=tmp_path, _env_file=None)
    app.dependency_overrides[get_settings] = lambda: current
    try:
        yield current
    finally:
        app.dependency_overrides.pop(get_settings, None)


def _journal(settings: Settings) -> list[dict]:
    path = settings.data_dir / "demo_requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_request_is_written_to_the_journal(settings: Settings) -> None:
    with TestClient(app) as client:
        response = client.post("/api/demo-requests", json=VALID)
    assert response.status_code == 201
    [record] = _journal(settings)
    assert record["email"] == "anna@example.com"
    assert record["company"] == "Исследовательское агентство"
    assert "website" not in record
    assert record["received_at"]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"name": "   "}, "name"),
        ({"email": "anna@"}, "email"),
        ({"phone": "позвоните"}, "phone"),
        ({"consent": False}, "consent"),
    ],
)
def test_invalid_request_names_the_field(settings: Settings, change: dict, field: str) -> None:
    with TestClient(app) as client:
        response = client.post("/api/demo-requests", json=VALID | change)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == field
    assert _journal(settings) == []


def test_honeypot_is_accepted_but_not_stored(settings: Settings) -> None:
    with TestClient(app) as client:
        response = client.post("/api/demo-requests", json=VALID | {"website": "spam.example"})
    assert response.status_code == 201
    assert _journal(settings) == []


def test_mail_goes_out_only_when_configured_and_its_failure_is_not_shown(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(demo_requests, "_send_mail", lambda s, r, t: sent.append(r.email))
    with TestClient(app) as client:
        client.post("/api/demo-requests", json=VALID)
        assert sent == []

        settings.demo_mail_to = "sales@example.com"
        settings.smtp_host = "smtp.example.com"
        client.post("/api/demo-requests", json=VALID)
        assert sent == ["anna@example.com"]

        def fail(*_: object) -> None:
            raise OSError("SMTP недоступен")

        monkeypatch.setattr(demo_requests, "_send_mail", fail)
        response = client.post("/api/demo-requests", json=VALID)
    assert response.status_code == 201
    assert len(_journal(settings)) == 3


def test_mail_text_lists_filled_fields_only() -> None:
    request = demo_requests.DemoRequest(**(VALID | {"phone": "", "role": ""}))
    text = demo_requests._mail_text(request, "2026-10-01T10:00:00+00:00")
    assert "Имя: Анна Иванова" in text
    assert "Телефон" not in text
    assert text.endswith("Трекинг бренда и веса")
