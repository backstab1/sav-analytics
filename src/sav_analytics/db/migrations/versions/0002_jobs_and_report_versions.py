"""Задания (P5) и неизменяемые версии отчётов (P3).

Revision ID: 0002_jobs_versions
Revises: 0001_projects
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_jobs_versions"
down_revision = "0001_projects"
branch_labels = None
depends_on = None

Timestamp = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("subject", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("active_key", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
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
        sa.Column("runner", sa.String(16), nullable=False, server_default="worker"),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("lease_until", Timestamp, nullable=True),
        sa.Column("run_after", Timestamp, nullable=True),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("started_at", Timestamp, nullable=True),
        sa.Column("heartbeat_at", Timestamp, nullable=True),
        sa.Column("finished_at", Timestamp, nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.UniqueConstraint("active_key", name="uq_jobs_active_key"),
    )
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_idempotency_key", "jobs", ["idempotency_key"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_table(
        "report_versions",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("artifact_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("configuration_revision", sa.Integer, nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.Integer, nullable=True),
        sa.Column("environment", sa.Text, nullable=False),
        sa.Column("parameters", sa.Text, nullable=False),
        sa.Column("files", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_report_versions"),
        sa.UniqueConstraint("project_id", "artifact_id", name="uq_report_versions_artifact"),
    )
    op.create_index("ix_report_versions_project_id", "report_versions", ["project_id"])


def downgrade() -> None:
    op.drop_table("report_versions")
    op.drop_table("jobs")
