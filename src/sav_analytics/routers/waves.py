"""Волны отдельными источниками (PQ.19, решение 034)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import ai_jobs
from ..ai_jobs import JobFailure
from ..api_dependencies import get_long_chat_model, get_repository
from ..api_presentation import present
from ..assistant.models import ChatModel, ModelError
from ..assistant.waves import request_matches
from ..core.waves import read_wave
from ..repository import InvalidUploadError, ProjectNotFoundError, ProjectRepository
from .ai import ai_refusal

router = APIRouter(prefix="/api/projects/{project_id}/waves", tags=["waves"])


class MappingRequest(BaseModel):
    mapping: dict[str, str | None] = Field(default_factory=dict)


class WaveCreate(MappingRequest):
    staging_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    added: list[str] = Field(default_factory=list, max_length=5000)


class WaveRename(BaseModel):
    label: str = Field(min_length=1, max_length=120)


def _errors(call):
    try:
        return call()
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Проект, волна или черновик не найдены."
        ) from exc
    except InvalidUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class WaveViewRequest(BaseModel):
    mode: str = Field(pattern="^(wave|all|compare)$")
    value: str | int | float | None = None


@router.get("")
def list_waves(
    project_id: UUID, repository: Annotated[ProjectRepository, Depends(get_repository)]
) -> dict:
    """Волны проекта для селектора: значения переменной волны с числом анкет,
    выбранная волна и файлы волн."""
    return _errors(lambda: repository.wave_overview(project_id))


@router.put("/view")
def set_wave_view(
    project_id: UUID,
    body: WaveViewRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    return present(_errors(lambda: repository.set_wave_view(project_id, body.mode, body.value)))


@router.post("/stage")
def stage_wave(
    project_id: UUID,
    file: Annotated[UploadFile, File()],
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    """Загрузить файл новой волны и получить предложение сопоставления."""
    staging_id = _errors(
        lambda: repository.stage_wave(project_id, file.filename or "wave.sav", file.file)
    )
    return _errors(lambda: repository.wave_preview(project_id, staging_id))


@router.post("/stage/{staging_id}/preview")
def preview_wave(
    project_id: UUID,
    staging_id: str,
    body: MappingRequest,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    return _errors(lambda: repository.wave_preview(project_id, staging_id, body.mapping))


@router.post("/stage/{staging_id}/ai-match", response_model=None)
def ai_match(
    project_id: UUID,
    staging_id: str,
    body: MappingRequest,
    request: Request,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
    model: Annotated[ChatModel | None, Depends(get_long_chat_model)],
) -> dict | JSONResponse:
    """ИИ предлагает пары для несопоставленных переменных — фоновой задачей."""
    project = _errors(lambda: repository.get(project_id))
    refusal = ai_refusal(request, project, model)
    if refusal is not None:
        return refusal
    assert model is not None
    preview = _errors(lambda: repository.wave_preview(project_id, staging_id, body.mapping))

    def run(progress: ai_jobs.Progress) -> dict[str, Any]:
        progress(0, 1, "Модель сопоставляет переменные")
        variables = {item["name"]: item for item in project["inspection"]["variables"]}
        wave = read_wave(repository._staged(project_id, staging_id))
        base = [
            {
                "name": row["target"],
                "label": row["label"],
                "codes": [item["label"] for item in variables[row["target"]].get("value_labels")
                          or []][:20],
            }
            for row in preview["mapping"] if not row["source"]
        ]
        free = [item["name"] for item in preview["unmatched"]]
        candidates = [
            {
                "name": name,
                "label": wave.labels.get(name) or "",
                "codes": list((wave.value_labels.get(name) or {}).values())[:20],
            }
            for name in free
        ]
        if not base or not candidates:
            return {"pairs": []}
        try:
            proposal = request_matches(model, base, candidates)
        except ModelError as exc:
            raise JobFailure(f"Модель не ответила: {exc}", "AI_PROVIDER_ERROR") from exc
        targets = {item["name"] for item in base}
        sources = set(free)
        pairs = []
        for pair in proposal.get("pairs") or []:
            if not isinstance(pair, dict):
                continue
            target, source = str(pair.get("target")), str(pair.get("source"))
            if target in targets and source in sources:
                pairs.append({"target": target, "source": source})
                targets.discard(target)
                sources.discard(source)
        return {"pairs": pairs}

    return ai_jobs.start_job(
        str(project_id), "wave_match", "Сопоставление переменных волны", run, subject=staging_id
    )


@router.post("")
def add_wave(
    project_id: UUID,
    body: WaveCreate,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    return present(
        _errors(
            lambda: repository.add_wave(
                project_id, body.staging_id, body.label, body.mapping, body.added
            )
        )
    )


@router.patch("/{wave_id}")
def rename_wave(
    project_id: UUID,
    wave_id: str,
    body: WaveRename,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    return present(_errors(lambda: repository.rename_wave(project_id, wave_id, body.label)))


@router.delete("/{wave_id}")
def remove_wave(
    project_id: UUID,
    wave_id: str,
    repository: Annotated[ProjectRepository, Depends(get_repository)],
) -> dict:
    return present(_errors(lambda: repository.remove_wave(project_id, wave_id)))
