"""Окружение Alembic: соединение приходит из `db.engine`, а не из alembic.ini."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from sav_analytics.db.schema import metadata

config = context.config
connection = config.attributes.get("connection")

if connection is not None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    context.run_migrations()
else:
    # `alembic revision --autogenerate` из командной строки разработчика.
    engine = create_engine(config.get_main_option("sqlalchemy.url") or "")
    with engine.begin() as own_connection:
        context.configure(
            connection=own_connection,
            target_metadata=metadata,
            render_as_batch=own_connection.dialect.name == "sqlite",
        )
        context.run_migrations()
