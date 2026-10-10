"""Пользователи, серверные сессии и журнал аудита (P4).

Revision ID: 0003_auth
Revises: 0002_jobs_versions
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_auth"
down_revision = "0002_jobs_versions"
branch_labels = None
depends_on = None

Timestamp = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("updated_at", Timestamp, nullable=False),
        sa.Column("last_login_at", Timestamp, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("expires_at", Timestamp, nullable=False),
        sa.Column("last_seen_at", Timestamp, nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("at", Timestamp, nullable=False),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("method", sa.String(8), nullable=True),
        sa.Column("path", sa.Text, nullable=True),
        sa.Column("status", sa.Integer, nullable=True),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("details", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    for column in ("at", "user_id", "action", "project_id"):
        op.create_index(f"ix_audit_log_{column}", "audit_log", [column])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("sessions")
    op.drop_table("users")
