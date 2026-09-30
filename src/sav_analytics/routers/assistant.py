from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..api_dependencies import get_chat_model, get_repository, get_settings
from ..api_errors import error_response
from ..api_presentation import present
from ..assistant.changes import RevertConflict
from ..assistant.models import ChatModel, ModelError
from ..assistant.service import AssistantError, AssistantService
from ..repository import ProjectNotFoundError, ProjectRepository
from ..settings import Settings

router = APIRouter(prefix="/api/projects/{project_id}/assistant", tags=["assistant"])

_NOT_FOUND = "Проект или план не найден."


class AssistantMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    table_id: UUID | None = None


class RevertRequest(BaseModel):
    # Отменить план вместе со всеми правками после него, если по отдельности нельзя.
    cascade: bool = False


def _service(repository: ProjectRepository, project_id: UUID) -> AssistantService:
    try:
        repository.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    return AssistantService(repository, project_id)


@router.get("")
def assistant_state(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    model: Annotated[ChatModel | None, Depends(get_chat_model)],
) -> dict:
    """Разговор и планы проекта. `enabled: false` — провайдер не настроен."""
    return {"enabled": model is not None, **_service(repository, project_id).state()}


@router.post("/messages")
def send_message(
    project_id: UUID,
    message: AssistantMessage,
    request: Request,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    model: Annotated[ChatModel | None, Depends(get_chat_model)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    service = _service(repository, project_id)
    if model is None:
        return error_response(
            request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="ASSISTANT_DISABLED",
            detail="Ассистент не подключён: задайте провайдера модели в настройках сервера.",
        )
    try:
        return service.send(
            message.text.strip(),
            str(message.table_id) if message.table_id else None,
            model,
            settings.assistant_max_tool_calls,
        )
    except ModelError as exc:
        return error_response(
            request,
            status_code=status.HTTP_502_BAD_GATEWAY,
            error_code="ASSISTANT_PROVIDER_ERROR",
            detail=f"Модель не ответила: {exc}",
        )


@router.delete("/messages")
def reset_conversation(
    project_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    return _service(repository, project_id).reset()


@router.post("/plans/{plan_id}/apply")
def apply_plan(
    project_id: UUID,
    plan_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Применить план одной ревизией. Вызывает только интерфейс, не модель."""
    service = _service(repository, project_id)
    try:
        project, plan = service.apply(str(plan_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except AssistantError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"project": present(project), "plan": plan}


@router.post("/plans/{plan_id}/decline")
def decline_plan(
    project_id: UUID,
    plan_id: UUID,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    service = _service(repository, project_id)
    try:
        return {"plan": service.decline(str(plan_id))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except AssistantError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/revert", response_model=None)
def revert_plan(
    project_id: UUID,
    plan_id: UUID,
    request: Request,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    body: RevertRequest | None = None,
) -> dict | JSONResponse:
    """Откатить применённый план без участия модели (`docs/assistant.md`, раздел 3)."""
    service = _service(repository, project_id)
    try:
        project, plan = service.revert(str(plan_id), cascade=bool(body and body.cascade))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from exc
    except AssistantError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RevertConflict as exc:
        return error_response(
            request,
            status_code=status.HTTP_409_CONFLICT,
            error_code="ASSISTANT_REVERT_CONFLICT",
            detail="Откатить только этот план нельзя: " + "; ".join(exc.reasons) + ". "
            "Можно откатить его вместе со всеми правками после него.",
        )
    return {"project": present(project), "plan": plan}
