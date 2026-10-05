"""ИИ проекта: выключатель, фоновые задачи и разбор анкеты (PQ.16, решение 034)."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import ai_jobs
from ..ai_jobs import JobFailure
from ..api_dependencies import get_long_chat_model, get_repository, get_settings
from ..api_errors import error_response
from ..api_presentation import present
from ..assistant.models import ChatModel, ModelError
from ..assistant.questionnaire import request_mapping
from ..core.questionnaire import (
    MAX_QUESTIONNAIRE_BYTES,
    QuestionnaireError,
    catalog,
    extract_text,
    observed_candidates,
    proposal_rows,
    trimmed_text,
)
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository
from ..settings import Settings

router = APIRouter(prefix="/api/projects/{project_id}", tags=["ai"])

_NOT_FOUND = "Проект не найден."


class AiSettingsUpdate(BaseModel):
    enabled: bool | None = None
    # Пользователь увидел, что и какому провайдеру уйдёт, и согласился.
    acknowledged: bool | None = None


class QuestionnaireApply(BaseModel):
    job_id: UUID
    row_ids: list[str] = Field(min_length=1, max_length=20_000)


def ai_state(project: dict, settings: Settings) -> dict[str, Any]:
    stored = project.get("ai") or {}
    return {
        "configured": settings.assistant_enabled,
        "provider": urlsplit(settings.assistant_base_url or "").hostname,
        "model": settings.assistant_model,
        "fast_model": settings.ai_fast_model or settings.assistant_model,
        "enabled": bool(stored.get("enabled", True)),
        "acknowledged": bool(stored.get("acknowledged", False)),
    }


def _project(repository: ProjectRepository, project_id: UUID) -> dict:
    try:
        return repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc


def ai_refusal(
    request: Request, project: dict, model: ChatModel | None
) -> JSONResponse | None:
    """Ответ-отказ, если ИИ в проекте недоступен, иначе None."""
    if model is None:
        return error_response(
            request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="AI_NOT_CONFIGURED",
            detail="ИИ не подключён: задайте провайдера модели в настройках сервера.",
        )
    if not (project.get("ai") or {}).get("enabled", True):
        return error_response(
            request,
            status_code=status.HTTP_409_CONFLICT,
            error_code="AI_DISABLED",
            detail="В этом проекте ИИ выключен. Включите его в меню проекта.",
        )
    return None


@router.get("/ai")
def get_ai(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    return ai_state(_project(repository, project_id), settings)


@router.put("/ai")
def put_ai(
    project_id: UUID,
    update: AiSettingsUpdate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    try:
        project = repository.set_ai_settings(
            project_id, enabled=update.enabled, acknowledged=update.acknowledged
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    return ai_state(project, settings)


@router.get("/jobs")
def list_jobs(
    project_id: UUID, repository: Annotated[ProjectRepository, Depends(get_repository)]
) -> dict:
    _project(repository, project_id)
    return {"jobs": ai_jobs.list_jobs(str(project_id))}


@router.get("/jobs/{job_id}")
def get_job(project_id: UUID, job_id: UUID) -> dict:
    job = ai_jobs.get_job(str(project_id), str(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена.")
    return job


@router.post("/jobs/{job_id}/retry")
def retry_job(project_id: UUID, job_id: UUID) -> dict:
    job = ai_jobs.retry_job(str(project_id), str(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена.")
    return job


@router.post("/questionnaire", response_model=None)
async def upload_questionnaire(
    project_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    model: Annotated[ChatModel | None, Depends(get_long_chat_model)],
) -> dict | JSONResponse:
    """Принять анкету и запустить её разбор моделью фоновой задачей."""
    project = _project(repository, project_id)
    refusal = ai_refusal(request, project, model)
    if refusal is not None:
        return refusal
    data = await file.read(MAX_QUESTIONNAIRE_BYTES + 1)
    filename = file.filename or "questionnaire"
    try:
        text = extract_text(filename, data)
    except QuestionnaireError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert model is not None

    def run(progress: ai_jobs.Progress) -> dict[str, Any]:
        progress(0, 3, "Читаем значения массива")
        try:
            current = repository.get(project_id)
            observed = repository.observed_values(project_id, observed_candidates(current))
        except ProjectNotFoundError as exc:
            raise JobFailure("Проект удалён.") from exc
        progress(1, 3, "Модель сопоставляет анкету с массивом")
        body, truncated = trimmed_text(text)
        try:
            proposal = request_mapping(model, body, catalog(current, observed), truncated=truncated)
        except ModelError as exc:
            raise JobFailure(f"Модель не ответила: {exc}", "AI_PROVIDER_ERROR") from exc
        progress(2, 3, "Проверяем предложения")
        rows, skipped = proposal_rows(current, proposal, observed)
        return {
            "filename": filename,
            "revision": current["configuration"]["revision"],
            "truncated": truncated,
            "rows": rows,
            "skipped": skipped,
            "notes": str(proposal.get("notes") or "")[:2000],
        }

    return ai_jobs.start_job(str(project_id), "questionnaire", f"Анкета «{filename}»", run)


@router.post("/questionnaire/apply")
def apply_questionnaire(
    project_id: UUID,
    body: QuestionnaireApply,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Применить выбранные строки разбора одной ревизией — одним шагом отмены."""
    _project(repository, project_id)
    result = ai_jobs.job_result(str(project_id), str(body.job_id), "questionnaire")
    if result is None:
        raise HTTPException(
            status_code=404, detail="Разбор анкеты не найден: загрузите анкету заново."
        )
    chosen = set(body.row_ids)
    rows = [row for row in result["rows"] if row["id"] in chosen]
    if not rows:
        raise HTTPException(status_code=422, detail="Не выбрано ни одной строки.")
    try:
        project = repository.apply_questionnaire(project_id, rows)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Проект или вопрос не найден.") from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return present(project)
