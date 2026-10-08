"""Книги отчёта проекта: создать, скопировать, выбрать, переименовать, очистить, удалить."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ..core import report_books
from ..core.report_books import ReportBookError
from .store import InvalidUploadError, ProjectNotFoundError, ProjectStore


class ReportBooks(ProjectStore):
    """Каждое действие — одна запись проекта и один шаг отмены."""

    def create_report_book(
        self, project_id: UUID, name: str | None, source_id: UUID | None = None
    ) -> dict:
        return self._change_books(
            project_id,
            lambda configuration: report_books.create_book(
                configuration, name, source_id=str(source_id) if source_id else None
            ),
        )

    def activate_report_book(self, project_id: UUID, book_id: UUID) -> dict:
        return self._change_books(
            project_id,
            lambda configuration: report_books.activate(configuration, str(book_id)),
        )

    def rename_report_book(self, project_id: UUID, book_id: UUID, name: str) -> dict:
        return self._change_books(
            project_id,
            lambda configuration: report_books.rename_book(configuration, str(book_id), name),
        )

    def clear_report_book(self, project_id: UUID, book_id: UUID) -> dict:
        return self._change_books(
            project_id,
            lambda configuration: report_books.clear_book(configuration, str(book_id)),
        )

    def delete_report_book(self, project_id: UUID, book_id: UUID) -> dict:
        return self._change_books(
            project_id,
            lambda configuration: report_books.delete_book(configuration, str(book_id)),
        )

    def _change_books(
        self, project_id: UUID, change: Callable[[dict[str, Any]], object]
    ) -> dict:
        project = self.get(project_id)
        configuration = project["configuration"]
        try:
            change(configuration)
        except ReportBookError as exc:
            if "не найдена" in str(exc):
                raise ProjectNotFoundError(str(exc)) from exc
            raise InvalidUploadError(str(exc)) from exc
        configuration["updated_at"] = datetime.now(UTC).isoformat()
        self._write_project(project_id, project)
        return project
