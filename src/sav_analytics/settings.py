from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAV_ANALYTICS_", env_file=".env")

    data_dir: Path = Path(".data")
    max_upload_bytes: int = 250 * 1024 * 1024

    # База метаданных (P3). Пусто — файл SQLite `data_dir/sav-analytics.db`;
    # в production — PostgreSQL: postgresql+psycopg://user:pass@host/db.
    database_url: str | None = None
    # Приводить схему базы к версии приложения при старте. В production
    # выключено: миграции идут отдельным шагом `sav-analytics migrate`.
    auto_migrate: bool = True
    # Перенести проекты файлового хранилища (до P3) при первом обращении
    # процесса к базе. Перенос не трогает project.json и пропускает уже
    # перенесённые, поэтому повторы безопасны.
    auto_import_legacy: bool = True
    # Задания (P5, `jobs/dispatch.py`): `embedded` — процесс API исполняет
    # очередь сам; `external` — только ставит, исполняет `sav-analytics-worker`.
    job_runner: Literal["embedded", "external"] = "embedded"
    # Сколько тяжёлых заданий одновременно в одном процессе.
    worker_concurrency: int = 2
    # `process` — каждое задание в своём процессе: timeout и отмена его
    # убивают, предел памяти ограничивает его одного. `thread` — в потоке.
    worker_isolation: Literal["process", "thread"] = "process"
    # Предел памяти процесса задания, МБ (0 — без предела; только Linux).
    job_memory_limit_mb: int = 0
    # Не брать задания, пока на томе данных свободно меньше, МБ.
    min_free_disk_mb: int = 1024
    job_lease_seconds: int = 60
    job_poll_seconds: float = 2.0
    report_timeout_seconds: int = 1800
    import_timeout_seconds: int = 900
    # Сколько дней хранить строки завершённых заданий.
    job_ttl_days: int = 30
    # Авторизация (P4, `auth/`). Без неё к данным пускает любого — только
    # для локального прототипа на своей машине.
    auth_enabled: bool = True
    session_ttl_hours: int = 12
    session_idle_minutes: int = 120
    session_cookie: str = "sav_session"
    # Cookie с флагом Secure ставится на любой запрос по HTTPS (в том числе
    # пришедший через прокси с X-Forwarded-Proto). `true` — всегда, даже по
    # http: так браузер не вернёт cookie, и локальный вход не удержится.
    cookie_secure: bool = False
    # Первого администратора можно завести со страницы входа, пока
    # пользователей нет, — только с самой машины сервера (127.0.0.1).
    allow_remote_setup: bool = False
    login_max_failures: int = 10
    login_lockout_minutes: int = 15
    # Strict-Transport-Security; включать, когда сервер доступен только по HTTPS.
    hsts: bool = False
    log_format: Literal["text", "json"] = "text"
    log_level: str = "INFO"
    # Сколько дней проект лежит в корзине до окончательного удаления.
    trash_retention_days: int = 30

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
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def resolved_database_url(self) -> str:
        from .db import sqlite_url

        return self.database_url or sqlite_url(self.data_dir / "sav-analytics.db")

    @property
    def assistant_enabled(self) -> bool:
        return bool(self.assistant_base_url and self.assistant_model)

    @property
    def demo_mail_enabled(self) -> bool:
        return bool(self.demo_mail_to and self.smtp_host)
