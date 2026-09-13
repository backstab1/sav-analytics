from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..api_dependencies import get_repository
from ..api_schemas import TablePreviewRequest
from ..core.reporting.live import build_live_table
from ..core.reporting.models import ReportError
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
        )
    except ReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
