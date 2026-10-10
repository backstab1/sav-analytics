from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from .assistant.models import ChatModel, OpenAICompatibleModel
from .repository import ProjectRepository
from .repository.legacy_import import import_legacy
from .settings import Settings


@lru_cache
def get_settings() -> Settings:
    return Settings()


_imported: set[tuple[str, str]] = set()
_import_guard = threading.Lock()
logger = logging.getLogger(__name__)


def get_repository(settings: Annotated[Settings, Depends(get_settings)]) -> ProjectRepository:
    repository = ProjectRepository(
        settings.projects_dir,
        settings.max_upload_bytes,
        settings.resolved_database_url,
        migrate=settings.auto_migrate,
    )
    if settings.auto_import_legacy:
        key = (str(settings.projects_dir.resolve()), settings.resolved_database_url)
        with _import_guard:
            if key not in _imported:
                report = import_legacy(repository)
                if report.imported or report.failed:
                    logger.info("Legacy projects imported: %s", report.as_dict())
                _imported.add(key)
    return repository


@lru_cache
def _chat_model(
    base_url: str, api_key: str | None, model: str, timeout: float, temperature: float | None
) -> ChatModel:
    return OpenAICompatibleModel(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        temperature=temperature,
    )


def get_chat_model(settings: Annotated[Settings, Depends(get_settings)]) -> ChatModel | None:
    """Модель ассистента или None, если он не настроен. Тесты подменяют эту зависимость."""
    if not settings.assistant_enabled:
        return None
    return _chat_model(
        settings.assistant_base_url or "",
        settings.assistant_api_key.get_secret_value() if settings.assistant_api_key else None,
        settings.assistant_model or "",
        settings.assistant_timeout_seconds,
        settings.assistant_temperature,
    )


def get_long_chat_model(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatModel | None:
    """Основная модель с длинным таймаутом — для разовых больших запросов в
    фоновых задачах (разбор анкеты). Тесты подменяют эту зависимость."""
    if not settings.assistant_enabled:
        return None
    return _chat_model(
        settings.assistant_base_url or "",
        settings.assistant_api_key.get_secret_value() if settings.assistant_api_key else None,
        settings.assistant_model or "",
        settings.ai_long_timeout_seconds,
        settings.assistant_temperature,
    )


def get_fast_chat_model(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatModel | None:
    """Модель массовых задач; без `ai_fast_model` — основная. Тесты подменяют."""
    if not settings.assistant_enabled:
        return None
    return _chat_model(
        settings.assistant_base_url or "",
        settings.assistant_api_key.get_secret_value() if settings.assistant_api_key else None,
        settings.ai_fast_model or settings.assistant_model or "",
        settings.ai_long_timeout_seconds,
        settings.assistant_temperature,
    )
