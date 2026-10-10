"""Проверка доступа на каждом запросе к API (P4).

- Без сессии открыты только здоровье, вход и первичная настройка, а также
  заявка на демо с лендинга. Статика (оболочка интерфейса) отдаётся всем:
  в ней нет данных, а без входа она сама уводит на страницу входа.
- Сессия — cookie `HttpOnly; SameSite=Lax` с токеном, в базе — его хэш.
- Запрос, меняющий данные, несёт `X-CSRF-Token` своей сессии.
- `/api/admin/*` — только администратору.
- Журнал: каждое изменение, чтение исходников и скачивание отчётов.

Плюс заголовки безопасности и строгая CSP на всех ответах, включая
статику, и предел размера тела запроса.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from fastapi import Request, status
from starlette.responses import Response

from ..actor import Actor, bind_actor, reset_actor
from ..api_errors import error_response, request_id_from
from ..db import get_engine
from .store import AuthStore, csrf_matches

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC = {
    "/api/health",
    "/api/ready",
    "/api/auth/state",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/demo-requests",
}
_PROJECT = re.compile(r"^/api/projects/(?:trash/)?([0-9a-f-]{36})")
# Чтение исходника и скачивание готовых файлов — в журнал, хотя это GET.
_DOWNLOAD = re.compile(
    r"(\.(xlsx|txt|pptx|docx|zip|sav|csv)$)|(/source(/|$))|(/export(/|$))"
)

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        # Интерфейс задаёт оформление атрибутом style в разметке из JS.
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


def auth_store(settings: Any) -> AuthStore:
    return AuthStore(get_engine(settings.resolved_database_url, migrate=settings.auto_migrate))


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def security_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    from ..api_dependencies import get_settings

    settings = get_settings()
    limit = settings.max_upload_bytes + 1024 * 1024
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        return _secure(
            error_response(
                request,
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                error_code="PAYLOAD_TOO_LARGE",
                detail="Запрос больше допустимого размера.",
            ),
            settings,
        )
    path = request.url.path
    if not settings.auth_enabled or not path.startswith("/api/") or path in PUBLIC:
        return _secure(await call_next(request), settings)

    store = auth_store(settings)
    token = request.cookies.get(settings.session_cookie)
    resolved = (
        store.resolve_session(token, idle=timedelta(minutes=settings.session_idle_minutes))
        if token
        else None
    )
    if resolved is None:
        return _secure(
            error_response(
                request,
                status_code=status.HTTP_401_UNAUTHORIZED,
                error_code="AUTH_REQUIRED",
                detail="Войдите в систему.",
            ),
            settings,
        )
    session, user = resolved
    method = request.method.upper()
    if method not in SAFE_METHODS and not csrf_matches(
        session["csrf_token"], request.headers.get(CSRF_HEADER)
    ):
        return _secure(
            error_response(
                request,
                status_code=status.HTTP_403_FORBIDDEN,
                error_code="CSRF_FAILED",
                detail="Запрос отклонён защитой от подделки. Обновите страницу.",
            ),
            settings,
        )
    if path.startswith("/api/admin/") and user["role"] != "admin":
        return _secure(
            error_response(
                request,
                status_code=status.HTTP_403_FORBIDDEN,
                error_code="ADMIN_REQUIRED",
                detail="Действие доступно только администратору.",
            ),
            settings,
        )
    request.state.user = user
    request.state.session = session
    token_actor = bind_actor(Actor(user["id"], user["username"], user["role"]))
    try:
        response = await call_next(request)
    finally:
        reset_actor(token_actor)
    if method not in SAFE_METHODS or _DOWNLOAD.search(path):
        match = _PROJECT.match(path)
        action = "download" if method in SAFE_METHODS else "change"
        if path.startswith("/api/auth/"):
            action = "auth." + path.rsplit("/", 1)[-1]
        store.audit(
            action,
            user=user,
            method=method,
            path=path,
            status=response.status_code,
            project_id=match.group(1) if match else None,
            request_id=request_id_from(request),
            ip=client_ip(request),
        )
    return _secure(response, settings)


def _secure(response: Response, settings: Any) -> Response:
    headers = response.headers
    headers.setdefault("Content-Security-Policy", CSP)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )
    if settings.hsts:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
