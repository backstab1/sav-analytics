from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from .api_dependencies import get_repository, get_settings
from .api_errors import REQUEST_ID_HEADER, error_response, http_error_code, request_id_from
from .configuration_revision import (
    ConfigurationConflictError,
    bind_expected_revision,
    reset_expected_revision,
)
from .core.preflight import PreflightBlockedError
from .project_models import InvalidStoredProjectError
from .routers import banners, filters, projects, questions, recodings, reports, weights

app = FastAPI(title="sav-analytics API", version="0.1.0")
logger = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied_request_id = request.headers.get(REQUEST_ID_HEADER)
    request.state.request_id = (
        supplied_request_id
        if supplied_request_id and _REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else str(uuid4())
    )
    raw_revision = request.headers.get("If-Match")
    if raw_revision is None:
        expected_revision = None
    else:
        normalized = raw_revision.strip().strip('"')
        try:
            expected_revision = int(normalized)
        except ValueError:
            return error_response(
                request,
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="INVALID_CONFIGURATION_REVISION",
                detail="Заголовок If-Match должен содержать номер ревизии.",
            )
        if expected_revision < 1:
            return error_response(
                request,
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="INVALID_CONFIGURATION_REVISION",
                detail="Номер ревизии должен быть положительным.",
            )
    token = bind_expected_revision(expected_revision)
    try:
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id_from(request)
        return response
    finally:
        reset_expected_revision(token)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(
        request,
        status_code=exc.status_code,
        error_code=http_error_code(exc.status_code),
        detail=exc.detail,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response(
        request,
        status_code=422,
        error_code="REQUEST_VALIDATION_FAILED",
        detail=jsonable_encoder(exc.errors()),
    )


@app.exception_handler(ConfigurationConflictError)
async def configuration_conflict_handler(
    request: Request, exc: ConfigurationConflictError
) -> JSONResponse:
    return error_response(
        request,
        status_code=status.HTTP_409_CONFLICT,
        error_code="CONFIGURATION_CONFLICT",
        detail=str(exc),
    )


@app.exception_handler(PreflightBlockedError)
async def preflight_blocked_handler(
    request: Request, exc: PreflightBlockedError
) -> JSONResponse:
    return error_response(
        request,
        status_code=422,
        error_code="REPORT_PREFLIGHT_FAILED",
        detail=str(exc),
    )


@app.exception_handler(InvalidStoredProjectError)
async def invalid_project_handler(
    request: Request, exc: InvalidStoredProjectError
) -> JSONResponse:
    logger.exception(
        "Invalid stored project on %s",
        request.url.path,
        exc_info=exc,
        extra={"request_id": request_id_from(request)},
    )
    return error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code="PROJECT_DATA_INVALID",
        detail="Сохранённая конфигурация проекта повреждена.",
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unexpected API error on %s",
        request.url.path,
        exc_info=exc,
        extra={"request_id": request_id_from(request)},
    )
    return error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code="INTERNAL_ERROR",
        detail=(
            "Не удалось выполнить запрос из-за внутренней ошибки. "
            "Сообщите идентификатор запроса администратору."
        ),
    )


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


for router in (
    projects.router,
    questions.router,
    recodings.router,
    banners.router,
    filters.router,
    weights.router,
    reports.router,
):
    app.include_router(router)


static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    class DevelopmentStaticFiles(StaticFiles):
        async def get_response(
            self, path: str, scope: MutableMapping[str, Any]
        ) -> Response:
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            return response

    app.mount("/", DevelopmentStaticFiles(directory=static_dir, html=True), name="frontend")


__all__ = ["app", "get_repository", "get_settings"]
