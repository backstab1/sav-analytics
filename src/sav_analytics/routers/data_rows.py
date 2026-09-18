from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..core.data_rows import DataRowsError, browse_data_rows
from ..core.filtering import FilterError
from ..repository import ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}/data", tags=["data"], route_class=ProjectRoute
)


@router.get("/rows")
def data_rows(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    columns: Annotated[list[str] | None, Query()] = None,
    filter_id: Annotated[str | None, Query(max_length=64)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=10, le=100)] = 50,
) -> dict:
    try:
        project = repository.get(project_id)
        return browse_data_rows(
            repository.source_path(project_id),
            project,
            columns=columns,
            filter_id=filter_id,
            offset=offset,
            limit=limit,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект не найден.") from exc
    except (DataRowsError, FilterError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
