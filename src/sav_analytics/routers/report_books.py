from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..api_dependencies import get_repository
from ..api_presentation import ProjectRoute
from ..api_schemas import ReportBookCreate, ReportBookRename
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository

router = APIRouter(
    prefix="/api/projects/{project_id}/report-books",
    tags=["report-books"],
    route_class=ProjectRoute,
)

_NOT_FOUND = "Проект или книга отчёта не найдены."


def _call(action: Callable[[], dict]) -> dict:
    try:
        return action()
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_report_book(
    project_id: UUID,
    request: ReportBookCreate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Новая пустая книга или копия `source_id`; новая книга становится выбранной."""
    return _call(
        lambda: repository.create_report_book(project_id, request.name, request.source_id)
    )


@router.post("/{book_id}/activate")
def activate_report_book(
    project_id: UUID,
    book_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    return _call(lambda: repository.activate_report_book(project_id, book_id))


@router.patch("/{book_id}")
def rename_report_book(
    project_id: UUID,
    book_id: UUID,
    request: ReportBookRename,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    return _call(lambda: repository.rename_report_book(project_id, book_id, request.name))


@router.post("/{book_id}/clear")
def clear_report_book(
    project_id: UUID,
    book_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Снять баннер и фильтр книги и вернуть её настройки к значениям по умолчанию."""
    return _call(lambda: repository.clear_report_book(project_id, book_id))


@router.delete("/{book_id}")
def delete_report_book(
    project_id: UUID,
    book_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    return _call(lambda: repository.delete_report_book(project_id, book_id))
