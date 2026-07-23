from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAV_ANALYTICS_", env_file=".env")

    data_dir: Path = Path(".data")
    max_upload_bytes: int = 250 * 1024 * 1024

