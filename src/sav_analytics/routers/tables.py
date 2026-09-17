from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse

from ..api_dependencies import get_repository
from ..api_schemas import TableExportRequest, TablePreviewRequest
from ..core.reporting.live import build_live_table, export_live_table
from ..core.reporting.models import ReportError
from ..report_cache import store_table_export, table_export_path
from ..repository import ProjectNotFoundError, ProjectRepository

router = APIRouter(prefix="/api/projects/{project_id}/tables", tags=["tables"])


@router.post("/preview")
def preview_table(
    project_id: UUID,
    request: TablePreviewRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    try:
        return build_live_table(
            source,
            project,
            questions=request.questions,
            banner_id=str(request.banner_id) if request.banner_id else None,
            blocks=[block.model_dump(mode="json") for block in request.blocks]
            if request.blocks
            else None,
            filter_id=str(request.filter_id) if request.filter_id else None,
            sheet=request.sheet,
            overrides=_overrides(request),
        )
    except ReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/export")
def export_table(
    project_id: UUID,
    request: TableExportRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> Response:
    """Выгрузить книгой Excel таблицу экрана или все вопросы отчёта с её разрезом."""
    try:
        project = repository.get(project_id)
        source = repository.source_path(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    try:
        workbook, _ = export_live_table(
            source,
            project,
            questions=request.questions if request.scope == "table" else None,
            banner_id=str(request.banner_id) if request.banner_id else None,
            blocks=[block.model_dump(mode="json") for block in request.blocks]
            if request.blocks
            else None,
            filter_id=str(request.filter_id) if request.filter_id else None,
            overrides=_overrides(request),
        )
    except ReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    suffix = "таблица" if request.scope == "table" else "отчёт_по_разрезу"
    name = f"{project['name']}_{suffix}.xlsx"
    # Выгрузка уходит клиенту, поэтому остаётся в истории запусков: видно,
    # какой разрез и какие вопросы выгружали и когда.
    store_table_export(
        repository,
        project_id,
        project,
        workbook,
        {
            "scope": request.scope,
            "questions": len(request.questions),
            "banner": _cut_label(project, request),
            "filter": _filter_label(project, request.filter_id),
        },
    )
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


def _overrides(request: TablePreviewRequest) -> dict[str, dict] | None:
    if not request.overrides:
        return None
    return {
        code: override.model_dump(mode="json", exclude_none=True)
        for code, override in request.overrides.items()
    }


def _cut_label(project: dict, request: TableExportRequest) -> str | None:
    if request.banner_id:
        banner = next(
            (
                item
                for item in project["configuration"].get("banners", [])
                if str(item.get("id")) == str(request.banner_id)
            ),
            None,
        )
        return banner.get("name") if banner else None
    if request.blocks:
        return "; ".join(block.label or "блок" for block in request.blocks)
    return None


def _filter_label(project: dict, filter_id) -> str | None:
    if not filter_id:
        return None
    definition = next(
        (
            item
            for item in project["configuration"].get("filters", [])
            if str(item.get("id")) == str(filter_id)
        ),
        None,
    )
    return definition.get("name") if definition else None


@router.get("/exports/{artifact_id}")
def download_table_export(
    project_id: UUID,
    artifact_id: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> FileResponse:
    """Скачать сохранённую выгрузку «Таблиц» из истории запусков."""
    try:
        project = repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    path = table_export_path(repository, project_id, artifact_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Выгрузка не найдена или повреждена.")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{project['name']}_таблица.xlsx",
    )

