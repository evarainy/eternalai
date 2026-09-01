"""Add trusted tenant ownership to persisted Tasks.

Revision ID: 20260901_120000
Revises: 20260901_090000
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260901_120000"
down_revision = "20260901_090000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("tenant_id", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_tasks_tenant_id_non_blank",
        "tasks",
        "tenant_id IS NULL OR BTRIM(tenant_id) <> ''",
    )
    op.create_index(
        "ix_tasks_tenant_session",
        "tasks",
        ["tenant_id", "session_id"],
    )
    op.create_index(
        "ix_tasks_tenant_ai_user",
        "tasks",
        ["tenant_id", "ai_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_tenant_ai_user", table_name="tasks")
    op.drop_index("ix_tasks_tenant_session", table_name="tasks")
    op.drop_constraint(
        "ck_tasks_tenant_id_non_blank",
        "tasks",
        type_="check",
    )
    op.drop_column("tasks", "tenant_id")
