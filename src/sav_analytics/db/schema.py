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

# Задания (P5). База — источник истины статусов: worker и API в разных
# процессах видят одно и то же, перезапуск ничего не теряет. Брокера нет:
# свободное задание забирается `SELECT ... FOR UPDATE SKIP LOCKED` в
# PostgreSQL и транзакцией BEGIN IMMEDIATE в SQLite.
jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("kind", sa.String(64), nullable=False),
    sa.Column("project_id", sa.String(36), nullable=True, index=True),
    sa.Column("title", sa.Text, nullable=False, server_default=""),
    sa.Column("subject", sa.String(128), nullable=True),
    # queued → running → complete | failed | cancelled
    sa.Column("status", sa.String(16), nullable=False, index=True),
    # Ключ идемпотентности, пока задание не завершено: уникальность не даёт
    # поставить второй такой же расчёт; по завершении ключ снимается.
    sa.Column("active_key", sa.String(255), nullable=True, unique=True),
    sa.Column("idempotency_key", sa.String(255), nullable=True, index=True),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Column("result", sa.Text, nullable=True),
    sa.Column("error", sa.Text, nullable=True),
    sa.Column("error_code", sa.String(64), nullable=True),
    sa.Column("completed", sa.Integer, nullable=False, server_default="0"),
    sa.Column("total", sa.Integer, nullable=False, server_default="1"),
    sa.Column("stage", sa.Text, nullable=False, server_default=""),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
    sa.Column("timeout_seconds", sa.Integer, nullable=False, server_default="1800"),
    sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()),
    # Где выполняется: `worker` — любой worker берёт из очереди; `local` —
    # задание исполняет процесс API, поставивший его (ИИ-задачи с моделью
    # в памяти); после перезапуска такое задание помечается прерванным.
    sa.Column("runner", sa.String(16), nullable=False, server_default="worker"),
    sa.Column("worker_id", sa.String(128), nullable=True),
    sa.Column("lease_until", Timestamp, nullable=True),
    sa.Column("run_after", Timestamp, nullable=True),
    sa.Column("created_at", Timestamp, nullable=False, index=True),
    sa.Column("started_at", Timestamp, nullable=True),
    sa.Column("heartbeat_at", Timestamp, nullable=True),
    sa.Column("finished_at", Timestamp, nullable=True),
    sa.Column("created_by", sa.String(36), nullable=True),
)

# Неизменяемая версия собранного отчёта (P3): по ней сборка однозначно
# связана со снимком исходника и конфигурации и воспроизводима.
report_versions = sa.Table(
    "report_versions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("project_id", sa.String(36), nullable=False, index=True),
    sa.Column("artifact_id", sa.String(64), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("job_id", sa.String(36), nullable=True),
    sa.Column("created_at", Timestamp, nullable=False),
    sa.Column("created_by", sa.String(36), nullable=True),
    sa.Column("configuration_revision", sa.Integer, nullable=False),
    sa.Column("source_sha256", sa.String(64), nullable=True),
    sa.Column("schema_version", sa.Integer, nullable=True),
    # Версии приложения, кэша и расчётного стека: python, numpy, pandas, …
    sa.Column("environment", sa.Text, nullable=False),
    # Конфигурация, по которой собрано, целиком.
    sa.Column("parameters", sa.Text, nullable=False),
    # Размер и SHA-256 каждого файла сборки.
    sa.Column("files", sa.Text, nullable=False),
    sa.UniqueConstraint("project_id", "artifact_id", name="uq_report_versions_artifact"),
)
