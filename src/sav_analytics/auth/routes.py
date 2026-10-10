"""Вход, выход, первичная настройка и управление пользователями (P4)."""

from __future__ import annotations

import ipaddress
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ..api_dependencies import get_repository, get_settings
from ..api_errors import request_id_from
from ..repository import ProjectNotFoundError, ProjectRepository
from ..settings import Settings
from .middleware import auth_store, client_ip
from .store import AuthError

router = APIRouter(prefix="/api/auth", tags=["auth"])
admin = APIRouter(prefix="/api/admin", tags=["admin"])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Setup(Credentials):
    display_name: str = Field(default="", max_length=200)


class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=1, max_length=256)


class UserCreate(Setup):
    role: Literal["admin", "user"] = "user"


class UserUpdate(BaseModel):
    role: Literal["admin", "user"] | None = None
    active: bool | None = None
    display_name: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, min_length=1, max_length=256)


def _start_session(
    request: Request, response: Response, settings: Settings, user: dict[str, Any]
) -> dict[str, Any]:
    store = auth_store(settings)
    token, csrf = store.create_session(
        user["id"],
        ttl=timedelta(hours=settings.session_ttl_hours),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure or request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return {"user": _short(user), "csrf_token": csrf}


def _short(user: dict[str, Any]) -> dict[str, Any]:
    return {key: user[key] for key in ("id", "username", "display_name", "role")}


@router.get("/state")
def auth_state(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    """Нужен ли вход, нужна ли первичная настройка и кто вошёл."""
    if not settings.auth_enabled:
        return {"auth_enabled": False, "setup_required": False, "user": None, "csrf_token": None}
    store = auth_store(settings)
    token = request.cookies.get(settings.session_cookie)
    resolved = (
        store.resolve_session(token, idle=timedelta(minutes=settings.session_idle_minutes))
        if token
        else None
    )
    return {
        "auth_enabled": True,
        "setup_required": store.count_users() == 0,
        "setup_allowed": store.count_users() == 0 and _setup_allowed(request, settings),
        "user": _short(resolved[1]) if resolved else None,
        "csrf_token": resolved[0]["csrf_token"] if resolved else None,
    }


@router.post("/login")
def login(
    body: Credentials,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Авторизация выключена.")
    store = auth_store(settings)
    ip = client_ip(request)
    name = body.username.strip().lower()
    window = timedelta(minutes=settings.login_lockout_minutes)
    if store.recent_failures(name, ip, window) >= settings.login_max_failures:
        store.audit("auth.login_blocked", username=name, ip=ip,
                    request_id=request_id_from(request))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком много неудачных попыток. Повторите через "
            f"{settings.login_lockout_minutes} минут.",
        )
    user = store.authenticate(body.username, body.password)
    if user is None:
        store.audit("auth.login_failed", username=name, ip=ip,
                    request_id=request_id_from(request))
        raise HTTPException(status_code=401, detail="Неверное имя или пароль.")
    store.audit("auth.login", user=user, ip=ip, request_id=request_id_from(request))
    return _start_session(request, response, settings, user)


@router.post("/setup", status_code=status.HTTP_201_CREATED)
def setup(
    body: Setup,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Завести первого администратора, пока пользователей нет."""
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Авторизация выключена.")
    store = auth_store(settings)
    if store.count_users() > 0:
        raise HTTPException(status_code=409, detail="Администратор уже заведён.")
    if not _setup_allowed(request, settings):
        raise HTTPException(
            status_code=403,
            detail="Первого администратора заводят на самом сервере: "
            "`sav-analytics create-user --admin`.",
        )
    try:
        user = store.create_user(
            body.username, body.password, role="admin", display_name=body.display_name
        )
    except AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.audit("auth.setup", user=user, ip=client_ip(request),
                request_id=request_id_from(request))
    return _start_session(request, response, settings, user)


@router.post("/logout")
def logout(
    request: Request, response: Response, settings: Annotated[Settings, Depends(get_settings)]
) -> dict:
    token = request.cookies.get(settings.session_cookie)
    if token:
        auth_store(settings).revoke(token)
    response.delete_cookie(settings.session_cookie, path="/")
    return {"status": "logged_out"}


@router.get("/me")
def me(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    session = getattr(request.state, "session", None)
    if user is None or session is None:
        return {"user": None, "csrf_token": None}
    return {"user": _short(user), "csrf_token": session["csrf_token"]}


@router.post("/password")
def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=404, detail="Авторизация выключена.")
    store = auth_store(settings)
    try:
        store.change_password(user["id"], body.current, body.new)
    except AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Прежние сессии завершены сменой пароля; эта открывается заново.
    return _start_session(request, response, settings, user)


def _setup_allowed(request: Request, settings: Settings) -> bool:
    if settings.allow_remote_setup:
        return True
    host = client_ip(request)
    if host is None or host == "testclient":
        return host == "testclient"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# Администрирование -------------------------------------------------------------


@admin.get("/users")
def list_users(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {"users": auth_store(settings).list_users()}


@admin.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    try:
        return auth_store(settings).create_user(
            body.username, body.password, role=body.role, display_name=body.display_name
        )
    except AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@admin.patch("/users/{user_id}")
def update_user(
    user_id: str, body: UserUpdate, settings: Annotated[Settings, Depends(get_settings)]
) -> dict:
    try:
        return auth_store(settings).update_user(
            user_id,
            role=body.role,
            active=body.active,
            display_name=body.display_name,
            password=body.password,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Пользователь не найден.") from exc
    except AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@admin.get("/audit")
def audit_entries(
    settings: Annotated[Settings, Depends(get_settings)],
    limit: int = 200,
    project_id: str | None = None,
    user_id: str | None = None,
    action: str | None = None,
) -> dict:
    return {
        "entries": auth_store(settings).audit_entries(
            limit=limit, project_id=project_id, user_id=user_id, action=action
        )
    }


@admin.delete("/trash/{project_id}")
def purge_project(
    project_id: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Окончательно удалить проект из корзины — только администратор."""
    from uuid import UUID

    try:
        repository.purge(UUID(project_id))
    except (ProjectNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Проекта нет в корзине.") from exc
    return {"status": "purged", "project_id": project_id}
