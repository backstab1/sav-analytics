from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from .api_dependencies import get_repository, get_settings
from .routers import banners, filters, projects, questions, recodings, reports, weights

app = FastAPI(title="sav-analytics API", version="0.1.0")
logger = logging.getLogger(__name__)


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

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")


__all__ = ["app", "get_repository", "get_settings"]
