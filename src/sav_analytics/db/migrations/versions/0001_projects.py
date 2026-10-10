"""Проекты, история отмены и копии перед миграцией схемы (P3).

Revision ID: 0001_projects
Revises:
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_projects"
down_revision = None
branch_labels = None
depends_on = None

Timestamp = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("original_filename", sa.Text, nullable=False),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("updated_at", Timestamp, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("document", sa.Text, nullable=False),
        sa.Column("trashed_at", Timestamp, nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
    )
    op.create_index("ix_projects_trashed_at", "projects", ["trashed_at"])
    op.create_table(
        "project_history",
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("stacks", sa.Text, nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="fk_project_history_project_id_projects", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name="pk_project_history"),
    )
    op.create_table(
        "project_backups",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("document", sa.Text, nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="fk_project_backups_project_id_projects", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_backups"),
        sa.UniqueConstraint("project_id", "schema_version", name="uq_project_backups_version"),
    )
    op.create_index("ix_project_backups_project_id", "project_backups", ["project_id"])


def downgrade() -> None:
    op.drop_table("project_backups")
    op.drop_table("project_history")
    op.drop_index("ix_projects_trashed_at", table_name="projects")
    op.drop_table("projects")
