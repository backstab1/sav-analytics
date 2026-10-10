"""Таблицы метаданных (P3–P5).

Документ проекта хранится целиком текстом JSON в `projects.document`, а не
разложенным по таблицам: его контракт — `project_models.py`, он меняется
вместе с кодом и мигрируется при чтении (`ProjectStore.get`). Отдельными
колонками лежит только то, по чему ищут и что проверяют атомарно: ревизия,
название для библиотеки, отметка корзины.

Файлы — исходный SAV, волны, книги отчёта — остаются на защищённом томе
(`architecture.md` §2.5); база хранит ссылки на них и суммы.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

Timestamp = sa.DateTime(timezone=True)

projects = sa.Table(
    "projects",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("original_filename", sa.Text, nullable=False),
    sa.Column("created_at", Timestamp, nullable=False),
    sa.Column("updated_at", Timestamp, nullable=False),
    # Ревизия конфигурации: запись проходит, только если в базе та же ревизия,
    # что прочитал клиент (`UPDATE ... WHERE revision = :expected`).
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("document", sa.Text, nullable=False),
    # Корзина: проект с отметкой не виден в библиотеке и не открывается,
    # его файлы лежат на месте до окончательного удаления.
    sa.Column("trashed_at", Timestamp, nullable=True, index=True),
    sa.Column("created_by", sa.String(36), nullable=True),
)

# История «Отменить / Вернуть» — отдельно от документа: документ хэшируется
# в ключ кэша отчёта, и шаг отмены стал бы частью конфигурации.
project_history = sa.Table(
    "project_history",
    metadata,
    sa.Column(
        "project_id",
        sa.String(36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("stacks", sa.Text, nullable=False),
)

# Копия документа перед первой перезаписью новой схемой: если приложение
# откатят на версию, которая новую схему не читает, восстанавливать есть откуда
# (roadmap, «Устройство, которое не меняется»).
project_backups = sa.Table(
    "project_backups",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column(
        "project_id",
        sa.String(36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("schema_version", sa.Integer, nullable=False),
    sa.Column("created_at", Timestamp, nullable=False),
    sa.Column("document", sa.Text, nullable=False),
    sa.UniqueConstraint("project_id", "schema_version", name="uq_project_backups_version"),
)
