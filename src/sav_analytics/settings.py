from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAV_ANALYTICS_", env_file=".env")

    data_dir: Path = Path(".data")
    max_upload_bytes: int = 250 * 1024 * 1024

    # Ассистент «Таблиц» (`docs/assistant.md`). Любой провайдер с
    # OpenAI-совместимым chat completions и вызовом функций: адрес вида
    # https://api.deepseek.com или
    # https://generativelanguage.googleapis.com/v1beta/openai. Без адреса и
    # модели ассистент выключен и модели ничего не уходит.
    assistant_base_url: str | None = None
    assistant_api_key: SecretStr | None = None
    assistant_model: str | None = None
    assistant_timeout_seconds: float = 60.0
    assistant_temperature: float | None = 0.0
    assistant_max_tool_calls: int = 15

    @property
    def assistant_enabled(self) -> bool:
        return bool(self.assistant_base_url and self.assistant_model)
