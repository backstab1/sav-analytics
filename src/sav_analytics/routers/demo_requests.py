"""Заявки на демо с лендинга.

Заявка всегда дописывается строкой в `<data_dir>/demo_requests.jsonl` — это
журнал, который не теряется, даже если почта не настроена или упала. Письмо
уходит, только когда заданы адрес получателя и SMTP-сервер; его сбой пишется
в лог и посетителю не показывается: заявка уже принята.
"""

from __future__ import annotations

import json
import logging
import re
import smtplib
import threading
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator

from ..api_dependencies import get_settings
from ..settings import Settings

router = APIRouter(prefix="/api/demo-requests", tags=["demo"])
logger = logging.getLogger(__name__)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^[0-9+()\-\s.]{5,32}$")
_journal_lock = threading.Lock()


class DemoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    phone: str = Field(default="", max_length=32)
    company: str = Field(default="", max_length=160)
    role: str = Field(default="", max_length=120)
    message: str = Field(default="", max_length=2000)
    consent: bool
    # Скрытое поле-ловушка: человек его не видит, бот заполняет.
    website: str = Field(default="", max_length=200)

    @field_validator("name", "email", "phone", "company", "role", "message", "website")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def _name_present(cls, value: str) -> str:
        if not value:
            raise ValueError("Укажите имя.")
        return value

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        if not _EMAIL.fullmatch(value):
            raise ValueError("Проверьте адрес почты.")
        return value

    @field_validator("phone")
    @classmethod
    def _phone_shape(cls, value: str) -> str:
        if value and not _PHONE.fullmatch(value):
            raise ValueError("Проверьте номер телефона.")
        return value

    @field_validator("consent")
    @classmethod
    def _consent_given(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Нужно согласие на обработку данных.")
        return value


def _mail_text(request: DemoRequest, received_at: str) -> str:
    rows = [
        ("Имя", request.name),
        ("Почта", request.email),
        ("Телефон", request.phone),
        ("Компания", request.company),
        ("Роль", request.role),
        ("Получена", received_at),
    ]
    lines = [f"{label}: {value}" for label, value in rows if value]
    if request.message:
        lines += ["", "Что хотят увидеть:", request.message]
    return "\n".join(lines)


def _send_mail(settings: Settings, request: DemoRequest, received_at: str) -> None:
    mail = EmailMessage()
    mail["Subject"] = f"Заявка на демо: {request.name}" + (
        f", {request.company}" if request.company else ""
    )
    mail["From"] = settings.smtp_from or settings.smtp_user or settings.demo_mail_to or ""
    mail["To"] = settings.demo_mail_to or ""
    mail["Reply-To"] = request.email
    mail.set_content(_mail_text(request, received_at))

    host = settings.smtp_host or ""
    timeout = settings.smtp_timeout_seconds
    smtp: smtplib.SMTP
    if settings.smtp_ssl:
        smtp = smtplib.SMTP_SSL(host, settings.smtp_port, timeout=timeout)
    else:
        smtp = smtplib.SMTP(host, settings.smtp_port, timeout=timeout)
    with smtp:
        if settings.smtp_starttls and not settings.smtp_ssl:
            smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
        smtp.send_message(mail)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_demo_request(
    request: DemoRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    # Бот получает тот же ответ, что человек, но заявка никуда не пишется.
    if request.website:
        return {"status": "received"}

    received_at = datetime.now(UTC).isoformat(timespec="seconds")
    record = request.model_dump(exclude={"website"}) | {"received_at": received_at}
    journal = settings.data_dir / "demo_requests.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    with _journal_lock, journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if settings.demo_mail_enabled:
        try:
            _send_mail(settings, request, received_at)
        except (OSError, smtplib.SMTPException):
            logger.exception("Demo request mail was not sent")
    return {"status": "received"}
