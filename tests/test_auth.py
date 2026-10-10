"""Авторизация P4: анонимного доступа к данным нет, матрица прав, CSRF, журнал."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sav_analytics.api import app, get_repository
from sav_analytics.api_dependencies import get_settings
from sav_analytics.auth.middleware import PUBLIC, auth_store
from sav_analytics.repository import ProjectRepository
from tests.test_sav_reader import write_fixture

PASSWORD = "correct horse battery"
UUID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def secured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRepository]:
    repository = ProjectRepository(tmp_path / "projects", 10_000_000)
    monkeypatch.setenv("SAV_ANALYTICS_AUTH_ENABLED", "true")
    monkeypatch.setenv("SAV_ANALYTICS_COOKIE_SECURE", "false")
    monkeypatch.setenv("SAV_ANALYTICS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SAV_ANALYTICS_DATABASE_URL", repository.database_url)
    get_settings.cache_clear()
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        yield repository
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def _store():  # type: ignore[no-untyped-def]
    return auth_store(get_settings())


def _login(client: TestClient, username: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _templates() -> dict[str, list[str]]:
    """Все маршруты API по схеме OpenAPI: подключённые роутеры в app.routes
    свёрнуты, а в схеме перечислены все."""
    return {
        path: [method.upper() for method in operations]
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/")
    }


def _api_routes() -> list[tuple[str, str]]:
    return [
        (method, re.sub(r"\{[^}]+\}", UUID, path))
        for path, methods in _templates().items()
        for method in methods
    ]


def test_no_anonymous_access_beyond_health_and_login(secured: ProjectRepository) -> None:
    with TestClient(app) as client:
        checked = 0
        for method, path in _api_routes():
            if path in PUBLIC:
                continue
            response = client.request(method, path)
            assert response.status_code == 401, (method, path, response.status_code)
            assert response.json()["error_code"] == "AUTH_REQUIRED"
            checked += 1
        assert checked > 100
        assert client.get("/api/health").status_code == 200
        # Оболочка интерфейса отдаётся, но данных в ней нет.
        assert client.get("/").status_code == 200


def test_permission_matrix(secured: ProjectRepository, tmp_path: Path) -> None:
    store = _store()
    store.create_user("boss", PASSWORD, role="admin")
    store.create_user("analyst", PASSWORD)
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    with TestClient(app) as client:
        csrf = _login(client, "analyst")
        # Без CSRF-заголовка изменение отклоняется, с ним — проходит.
        with source.open("rb") as stream:
            refused = client.post(
                "/api/projects", files={"file": ("f.sav", stream, "application/octet-stream")}
            )
        assert refused.status_code == 403 and refused.json()["error_code"] == "CSRF_FAILED"
        with source.open("rb") as stream:
            created = client.post(
                "/api/projects",
                files={"file": ("f.sav", stream, "application/octet-stream")},
                headers={"X-CSRF-Token": csrf},
            )
        assert created.status_code == 201
        project_id = created.json()["id"]
        assert client.get("/api/projects").status_code == 200
        # Администрирование — не для аналитика.
        for method, path in _api_routes():
            if path.startswith("/api/admin/"):
                response = client.request(method, path, headers={"X-CSRF-Token": csrf})
                assert response.status_code == 403, (method, path)

    with TestClient(app) as admin:
        csrf = _login(admin, "boss")
        users = admin.get("/api/admin/users").json()["users"]
        assert {user["username"] for user in users} == {"boss", "analyst"}
        assert all("password_hash" not in user for user in users)
        # Все аналитики видят все проекты (architecture.md §5).
        assert [item["id"] for item in admin.get("/api/projects").json()] == [project_id]
        audit = admin.get("/api/admin/audit", params={"project_id": project_id}).json()
        assert audit["entries"] == []  # проект создан запросом без id в адресе
        changes = admin.get("/api/admin/audit", params={"action": "change"}).json()
        assert [(entry["username"], entry["path"]) for entry in changes["entries"]] == [
            ("analyst", "/api/projects")
        ]
        logins = admin.get("/api/admin/audit", params={"action": "auth.login"}).json()
        assert {entry["username"] for entry in logins["entries"]} == {"analyst", "boss"}

        analyst = next(user for user in users if user["username"] == "analyst")
        disabled = admin.patch(
            f"/api/admin/users/{analyst['id']}",
            json={"active": False},
            headers={"X-CSRF-Token": csrf},
        )
        assert disabled.status_code == 200 and disabled.json()["active"] is False
        boss = next(user for user in users if user["username"] == "boss")
        last_admin = admin.patch(
            f"/api/admin/users/{boss['id']}", json={"role": "user"},
            headers={"X-CSRF-Token": csrf},
        )
        assert last_admin.status_code == 422


def test_download_and_logout_are_audited(secured: ProjectRepository, tmp_path: Path) -> None:
    _store().create_user("analyst", PASSWORD)
    source = tmp_path / "fixture.sav"
    write_fixture(source)
    with TestClient(app) as client:
        csrf = _login(client, "analyst")
        with source.open("rb") as stream:
            project = client.post(
                "/api/projects",
                files={"file": ("f.sav", stream, "application/octet-stream")},
                headers={"X-CSRF-Token": csrf},
            ).json()
        downloads = [
            path for path, methods in _templates().items()
            if path.endswith(".sav") and path.count("{") == 1 and "GET" in methods
        ]
        assert downloads
        path = downloads[0].replace("{project_id}", project["id"])
        assert client.get(path).status_code == 200
        assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/api/projects").status_code == 401
    entries = _store().audit_entries(project_id=project["id"])
    assert any(entry["action"] == "download" and entry["path"] == path for entry in entries)


def test_wrong_password_and_lockout(
    secured: ProjectRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAV_ANALYTICS_LOGIN_MAX_FAILURES", "3")
    get_settings.cache_clear()
    _store().create_user("analyst", PASSWORD)
    with TestClient(app) as client:
        for _ in range(3):
            wrong = client.post(
                "/api/auth/login", json={"username": "analyst", "password": "nope"}
            )
            assert wrong.status_code == 401
        blocked = client.post(
            "/api/auth/login", json={"username": "analyst", "password": PASSWORD}
        )
        assert blocked.status_code == 429


def test_first_admin_setup_only_once(secured: ProjectRepository) -> None:
    with TestClient(app) as client:
        state = client.get("/api/auth/state").json()
        assert state["setup_required"] is True and state["user"] is None
        short = client.post("/api/auth/setup", json={"username": "boss", "password": "short"})
        assert short.status_code == 422
        created = client.post("/api/auth/setup", json={"username": "Boss", "password": PASSWORD})
        assert created.status_code == 201
        assert created.json()["user"]["role"] == "admin"
        assert created.json()["user"]["username"] == "boss"
        again = client.post("/api/auth/setup", json={"username": "x2", "password": PASSWORD})
        assert again.status_code == 409
        state = client.get("/api/auth/state").json()
        assert state["user"]["username"] == "boss" and state["csrf_token"]
        assert client.get("/api/projects").status_code == 200


def test_password_change_ends_other_sessions(secured: ProjectRepository) -> None:
    _store().create_user("analyst", PASSWORD)
    with TestClient(app) as first, TestClient(app) as second:
        csrf = _login(first, "analyst")
        _login(second, "analyst")
        changed = first.post(
            "/api/auth/password",
            json={"current": PASSWORD, "new": "another long secret"},
            headers={"X-CSRF-Token": csrf},
        )
        assert changed.status_code == 200
        assert first.get("/api/projects").status_code == 200
        assert second.get("/api/projects").status_code == 401


def test_security_headers_and_body_limit(secured: ProjectRepository) -> None:
    with TestClient(app) as client:
        page = client.get("/")
        csp = page.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert page.headers["x-content-type-options"] == "nosniff"
        assert page.headers["x-frame-options"] == "DENY"
        assert "fonts.googleapis.com" not in page.text
        huge = client.post("/api/projects", headers={"content-length": str(10**12)}, content=b"")
        assert huge.status_code == 413
