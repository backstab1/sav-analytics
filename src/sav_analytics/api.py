from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from .api_dependencies import get_repository, get_settings
from .configuration_revision import (
    ConfigurationConflictError,
    bind_expected_revision,
    reset_expected_revision,
)
from .routers import banners, filters, projects, questions, recodings, reports, weights

app = FastAPI(title="sav-analytics API", version="0.1.0")
logger = logging.getLogger(__name__)


@app.middleware("http")
async def configuration_revision_context(request: Request, call_next):
    raw_revision = request.headers.get("If-Match")
    if raw_revision is None:
        expected_revision = None
    else:
        normalized = raw_revision.strip().strip('"')
        try:
            expected_revision = int(normalized)
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Заголовок If-Match должен содержать номер ревизии."},
            )
        if expected_revision < 1:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Номер ревизии должен быть положительным."},
            )
    token = bind_expected_revision(expected_revision)
    try:
        return await call_next(request)
    finally:
        reset_expected_revision(token)


@app.exception_handler(ConfigurationConflictError)
async def configuration_conflict_handler(
    _request: Request, exc: ConfigurationConflictError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc)},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unexpected API error on %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": (
                "Не удалось обработать SAV из-за внутренней ошибки. "
                "Подробности записаны в консоль сервера."
            )
        },
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
        async def get_response(self, path: str, scope: dict) -> object:
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            return response

    app.mount("/", DevelopmentStaticFiles(directory=static_dir, html=True), name="frontend")


__all__ = ["app", "get_repository", "get_settings"]
