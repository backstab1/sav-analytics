"""Пользователи, сессии и журнал аудита в базе метаданных (P4)."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from ..db.engine import aware, utcnow
from ..db.schema import audit_log, sessions, users

ROLES = ("admin", "user")
MIN_PASSWORD_LENGTH = 10
_USERNAME = re.compile(r"^[a-z0-9._@-]{2,64}$")
_hasher = PasswordHasher()  # Argon2id с параметрами argon2-cffi по умолчанию
# Хэш для несуществующего имени: проверка занимает то же время, и по
# задержке ответа нельзя узнать, есть ли такой пользователь.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


class AuthError(ValueError):
    """Ошибка с текстом для человека."""


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов.")
    return _hasher.hash(password)


def normalize_username(username: str) -> str:
    cleaned = username.strip().lower()
    if not _USERNAME.fullmatch(cleaned):
        raise AuthError(
            "Имя входа — от 2 до 64 символов: латиница, цифры, точка, дефис, @ и _."
        )
    return cleaned


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # Пользователи --------------------------------------------------------------

    def count_users(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(sa.select(sa.func.count()).select_from(users)).scalar())

    def create_user(
        self, username: str, password: str, *, role: str = "user", display_name: str = ""
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise AuthError("Роль — admin или user.")
        name = normalize_username(username)
        now = utcnow()
        values = {
            "id": str(uuid4()),
            "username": name,
            "display_name": display_name.strip() or name,
            "password_hash": hash_password(password),
            "role": role,
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
        try:
            with self.engine.begin() as connection:
                connection.execute(users.insert().values(**values))
        except IntegrityError as exc:
            raise AuthError("Пользователь с таким именем уже есть.") from exc
        return self.get_user(values["id"]) or {}

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(sa.select(users).where(users.c.id == user_id)).first()
        return None if row is None else _public_user(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(sa.select(users).order_by(users.c.username)).all()
        return [_public_user(row) for row in rows]

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        """Пользователь по имени и паролю или None; хэш при надобности обновляется."""
        try:
            name = normalize_username(username)
        except AuthError:
            name = ""
        with self.engine.connect() as connection:
            row = connection.execute(sa.select(users).where(users.c.username == name)).first()
        stored = row.password_hash if row is not None else _DUMMY_HASH
        try:
            _hasher.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return None
        if row is None or not row.active:
            return None
        values: dict[str, Any] = {"last_login_at": utcnow()}
        if _hasher.check_needs_rehash(stored):
            values["password_hash"] = _hasher.hash(password)
        with self.engine.begin() as connection:
            connection.execute(users.update().where(users.c.id == row.id).values(**values))
        return _public_user(row)

    def update_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        active: bool | None = None,
        display_name: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {"updated_at": utcnow()}
        if role is not None:
            if role not in ROLES:
                raise AuthError("Роль — admin или user.")
            values["role"] = role
        if active is not None:
            values["active"] = active
        if display_name is not None:
            values["display_name"] = display_name.strip() or None
        if password is not None:
            values["password_hash"] = hash_password(password)
        values = {key: value for key, value in values.items() if value is not None}
        with self.engine.begin() as connection:
            current = connection.execute(sa.select(users).where(users.c.id == user_id)).first()
            if current is None:
                raise LookupError(user_id)
            demoting = (role is not None and role != "admin") or active is False
            if current.role == "admin" and current.active and demoting:
                admins = connection.execute(
                    sa.select(sa.func.count())
                    .select_from(users)
                    .where(users.c.role == "admin")
                    .where(users.c.active.is_(True))
                ).scalar()
                if admins <= 1:
                    raise AuthError("Нельзя убрать последнего администратора.")
            connection.execute(users.update().where(users.c.id == user_id).values(**values))
            # Смена пароля, роли или отключение завершают сессии человека.
            if password is not None or active is False or role is not None:
                connection.execute(sessions.delete().where(sessions.c.user_id == user_id))
        return self.get_user(user_id) or {}

    def change_password(self, user_id: str, current: str, new: str) -> None:
        with self.engine.connect() as connection:
            row = connection.execute(sa.select(users).where(users.c.id == user_id)).first()
        if row is None:
            raise LookupError(user_id)
        try:
            _hasher.verify(row.password_hash, current)
        except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
            raise AuthError("Текущий пароль неверен.") from exc
        self.update_user(user_id, password=new)

    # Сессии ---------------------------------------------------------------------

    def create_session(
        self,
        user_id: str,
        *,
        ttl: timedelta,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[str, str]:
        """Новая сессия: (токен для cookie, CSRF-токен)."""
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = utcnow()
        with self.engine.begin() as connection:
            connection.execute(
                sessions.insert().values(
                    id=token_digest(token),
                    user_id=user_id,
                    csrf_token=csrf,
                    created_at=now,
                    expires_at=now + ttl,
                    last_seen_at=now,
                    ip=ip,
                    user_agent=(user_agent or "")[:500],
                )
            )
            # Заодно убираем просроченные — таблица не растёт бесконечно.
            connection.execute(sessions.delete().where(sessions.c.expires_at < now))
        return token, csrf

    def resolve_session(
        self, token: str, *, idle: timedelta
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """(сессия, пользователь) по токену из cookie или None.

        Сессия истекает по абсолютному сроку и по простою; отметка
        активности пишется не чаще раза в минуту.
        """
        digest = token_digest(token)
        now = utcnow()
        query = (
            sa.select(sessions, users.c.username, users.c.display_name, users.c.role,
                      users.c.active)
            .join(users, users.c.id == sessions.c.user_id)
            .where(sessions.c.id == digest)
        )
        with self.engine.connect() as connection:
            row = connection.execute(query).first()
        if row is None:
            return None
        expires = aware(row.expires_at)
        seen = aware(row.last_seen_at)
        if not row.active or expires is None or expires < now or seen is None or now - seen > idle:
            self.revoke(token)
            return None
        if now - seen > timedelta(minutes=1):
            with self.engine.begin() as connection:
                connection.execute(
                    sessions.update().where(sessions.c.id == digest).values(last_seen_at=now)
                )
        session = {"id": digest, "csrf_token": row.csrf_token, "user_id": row.user_id}
        user = {
            "id": row.user_id,
            "username": row.username,
            "display_name": row.display_name,
            "role": row.role,
        }
        return session, user

    def revoke(self, token: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(sessions.delete().where(sessions.c.id == token_digest(token)))

    # Журнал -----------------------------------------------------------------------

    def audit(
        self,
        action: str,
        *,
        user: dict[str, Any] | None = None,
        method: str | None = None,
        path: str | None = None,
        status: int | None = None,
        project_id: str | None = None,
        request_id: str | None = None,
        ip: str | None = None,
        details: dict[str, Any] | None = None,
        username: str | None = None,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                audit_log.insert().values(
                    at=utcnow(),
                    user_id=user["id"] if user else None,
                    username=(user["username"] if user else username),
                    action=action,
                    method=method,
                    path=path,
                    status=status,
                    project_id=project_id,
                    request_id=request_id,
                    ip=ip,
                    details=json.dumps(details, ensure_ascii=False) if details else None,
                )
            )

    def recent_failures(self, username: str, ip: str | None, window: timedelta) -> int:
        """Неудачные входы под этим именем или с этого адреса за окно."""
        since = utcnow() - window
        condition = audit_log.c.username == username
        if ip:
            condition = sa.or_(condition, audit_log.c.ip == ip)
        query = (
            sa.select(sa.func.count())
            .select_from(audit_log)
            .where(audit_log.c.action == "auth.login_failed")
            .where(audit_log.c.at >= since)
            .where(condition)
        )
        with self.engine.connect() as connection:
            return int(connection.execute(query).scalar())

    def audit_entries(
        self,
        *,
        limit: int = 200,
        project_id: str | None = None,
        user_id: str | None = None,
        action: str | None = None,
        before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        query = sa.select(audit_log).order_by(audit_log.c.id.desc()).limit(min(limit, 1000))
        if project_id:
            query = query.where(audit_log.c.project_id == project_id)
        if user_id:
            query = query.where(audit_log.c.user_id == user_id)
        if action:
            query = query.where(audit_log.c.action == action)
        if before:
            query = query.where(audit_log.c.at < before)
        with self.engine.connect() as connection:
            rows = connection.execute(query).all()
        entries = []
        for row in rows:
            entry = dict(row._mapping)
            at = aware(entry["at"])
            entry["at"] = at.isoformat() if at else None
            entry["details"] = json.loads(entry["details"]) if entry["details"] else None
            entries.append(entry)
        return entries


def csrf_matches(expected: str, supplied: str | None) -> bool:
    return bool(supplied) and hmac.compare_digest(expected, supplied or "")


def _public_user(row: Any) -> dict[str, Any]:
    data = dict(row._mapping)
    data.pop("password_hash", None)
    for key in ("created_at", "updated_at", "last_login_at"):
        value = aware(data.get(key))
        data[key] = value.isoformat() if value else None
    data["active"] = bool(data["active"])
    return data
