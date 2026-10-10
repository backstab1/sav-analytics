"""Подключение к базе метаданных и приведение схемы к текущей.

Локально и в тестах база — файл SQLite рядом с данными, в production —
PostgreSQL (`SAV_ANALYTICS_DATABASE_URL`). Код один: SQLAlchemy Core без
диалектных особенностей, кроме блокировок, которые разведены здесь и в
очереди заданий.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from .schema import metadata

MIGRATIONS = Path(__file__).parent / "migrations"

_engines: dict[str, Engine] = {}
_ready: set[str] = set()
_guard = threading.Lock()


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def get_engine(url: str, *, migrate: bool = True) -> Engine:
    """Движок для адреса: один на процесс, схема приведена к последней миграции.

    `migrate=False` — только проверить, что схема на месте (production
    запускает миграции отдельным шагом перед стартом API и worker).
    """
    with _guard:
        engine = _engines.get(url)
        if engine is None:
            engine = _create_engine(url)
            _engines[url] = engine
        if url not in _ready:
            if migrate:
                upgrade(engine)
            elif current_revision(engine) != head_revision():
                raise RuntimeError(
                    "Схема базы не совпадает с версией приложения. "
                    "Выполните `sav-analytics migrate`."
                )
            _ready.add(url)
    return engine


def dispose_engines() -> None:
    with _guard:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _ready.clear()


def is_sqlite(engine: Engine) -> bool:
    return engine.dialect.name == "sqlite"


def _create_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # Пул не держит файл открытым между запросами: тесты создают сотни
        # временных баз, а Windows не даёт удалить открытый файл.
        engine = sa.create_engine(url, poolclass=NullPool, connect_args={"timeout": 30})
        _configure_sqlite(engine)
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return engine
    return sa.create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def _configure_sqlite(engine: Engine) -> None:
    @sa.event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        # Транзакции открываем сами (ниже), чтобы запись брала блокировку
        # сразу, а не при первом UPDATE: иначе две транзакции, начавшие с
        # чтения, упираются друг в друга и одна падает с «database is locked».
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @sa.event.listens_for(engine, "begin")
    def _on_begin(connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def _alembic_config(engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    # configparser понимает «%» как подстановку, а в адресе он из процентного
    # кодирования кириллицы в пути.
    url = engine.url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


_head: str | None = None


def head_revision() -> str:
    global _head
    if _head is None:
        script = ScriptDirectory(str(MIGRATIONS))
        _head = script.get_current_head()
    assert _head is not None
    return _head


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade(engine: Engine) -> None:
    """Довести схему до последней миграции.

    Пустая база создаётся сразу по описанию таблиц и помечается последней
    ревизией — так быстрее, а совпадение описания с цепочкой миграций
    проверяет отдельный тест. Непустая проходит миграции по порядку.
    """
    head = head_revision()
    current = current_revision(engine)
    if current == head:
        return
    inspector = sa.inspect(engine)
    if current is None and not inspector.get_table_names():
        with engine.begin() as connection:
            metadata.create_all(connection)
            MigrationContext.configure(connection).stamp(
                ScriptDirectory(str(MIGRATIONS)), head
            )
        return
    config = _alembic_config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def run_migrations(engine: Engine) -> None:
    """Миграции по цепочке, даже для пустой базы — для `sav-analytics migrate`
    и проверки, что цепочка даёт ту же схему, что описание таблиц."""
    config = _alembic_config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    """SQLite возвращает время без пояса; в базе оно всегда UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
