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
    # Модель для массовых задач — кодирования тысяч открытых ответов — у того
    # же провайдера (решение 034: модель под задачу). Пусто — берётся
    # основная `assistant_model`.
    ai_fast_model: str | None = None
    # Таймаут разовых больших запросов: разбор анкеты, план автоотчёта.
    ai_long_timeout_seconds: float = 300.0

    # Заявки на демо с лендинга. Журнал в data_dir пишется всегда, письмо —
    # только при заданных получателе и SMTP-сервере.
    demo_mail_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_timeout_seconds: float = 15.0

    @property
    def assistant_enabled(self) -> bool:
        return bool(self.assistant_base_url and self.assistant_model)

    @property
    def demo_mail_enabled(self) -> bool:
        return bool(self.demo_mail_to and self.smtp_host)
