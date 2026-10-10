"""Общая настройка тестов.

`SAV_ANALYTICS_TEST_DATABASE_URL` — адрес сервера PostgreSQL (базу в нём
можно указать любую). С ним каждое хранилище, созданное без явного адреса,
получает свою чистую базу на этом сервере, и весь набор проходит на
PostgreSQL, а не на SQLite. Так CI проверяет production-диалект.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from sav_analytics.repository import store

POSTGRES_URL = os.environ.get("SAV_ANALYTICS_TEST_DATABASE_URL")


@pytest.fixture(autouse=True)
def _postgres_per_test(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if not POSTGRES_URL:
        yield
        return
    server = make_url(POSTGRES_URL)
    admin = sa.create_engine(server.set(database="postgres"), isolation_level="AUTOCOMMIT")
    created: dict[Path, str] = {}

    def database_for(root: Path) -> str:
        key = root.resolve()
        if key not in created:
            name = f"sav_test_{uuid4().hex[:12]}"
            with admin.connect() as connection:
                connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created[key] = server.set(database=name).render_as_string(hide_password=False)
        return created[key]

    monkeypatch.setattr(store, "default_database_url", database_for)
    yield
    from sav_analytics.db import dispose_engines

    dispose_engines()
    with admin.connect() as connection:
        for url in created.values():
            connection.exec_driver_sql(
                f'DROP DATABASE IF EXISTS "{make_url(url).database}" WITH (FORCE)'
            )
    admin.dispose()
