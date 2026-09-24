"""Файловое хранилище проектов.

`ProjectRepository` собран из частей по предметным областям; общее —
чтение, запись ревизией и блокировки — живёт в `store.ProjectStore`.
Части вызывают методы друг друга через `self`: у всех один корень, и
порядок баз на поведение не влияет.
"""

from .analysis import AnalysisItems
from .lifecycle import ProjectLifecycle
from .questions import QuestionEditing
from .report_setup import ReportSetup
from .store import (
    ANALYSIS_QUESTION_TYPES,
    STRUCTURE_VERSION,
    InvalidUploadError,
    ProjectNotFoundError,
    ProjectStore,
)
from .variables import DerivedVariables

__all__ = [
    "ANALYSIS_QUESTION_TYPES",
    "STRUCTURE_VERSION",
    "InvalidUploadError",
    "ProjectNotFoundError",
    "ProjectRepository",
]


class ProjectRepository(
    ProjectLifecycle,
    QuestionEditing,
    DerivedVariables,
    ReportSetup,
    AnalysisItems,
    ProjectStore,
):
    """Проекты на диске: папка на проект с `project.json` и исходным SAV."""
