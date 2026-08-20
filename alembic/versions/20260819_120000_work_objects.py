"""Add the Work Object aggregate table.

Revision ID: 20260819_120000
Revises: 20260731_090000
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260819_120000"
down_revision = "20260731_090000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_objects",
        sa.Column("work_object_id", sa.Text(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("assignee_ai_user_id", sa.Text(), nullable=False),
        sa.Column("assignee_display_name", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_title", sa.Text(), nullable=False),
        sa.Column("source_status", sa.Text(), nullable=False),
        sa.Column("source_received_at", sa.Text(), nullable=False),
        sa.Column("source_created_at", sa.Text(), nullable=False),
        sa.Column("source_workflow_type_id", sa.Text(), nullable=False),
        sa.Column("source_fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("handling_mark", sa.Text(), nullable=True),
        sa.Column("handling_marked_by_ai_user_id", sa.Text(), nullable=True),
        sa.Column("handling_marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_record_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_system = 'oa'", name="ck_work_objects_source_system"),
        sa.CheckConstraint(
            "source_kind = 'pending_workflow'",
            name="ck_work_objects_source_kind",
        ),
        sa.CheckConstraint(
            "handling_mark IS NULL OR handling_mark IN "
            "('pending_sync_confirmation', 'handled_elsewhere')",
            name="ck_work_objects_handling_mark",
        ),
        sa.CheckConstraint(
            "(handling_mark IS NULL AND handling_marked_by_ai_user_id IS NULL "
            "AND handling_marked_at IS NULL) OR "
            "(handling_mark IS NOT NULL AND handling_marked_by_ai_user_id IS NOT NULL "
            "AND handling_marked_at IS NOT NULL)",
            name="ck_work_objects_handling_record",
        ),
        sa.ForeignKeyConstraint(
            ["task_record_id"],
            ["tasks.task_id"],
            name="fk_work_objects_task_record_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("work_object_id"),
        sa.UniqueConstraint(
            "assignee_ai_user_id",
            "source_system",
            "source_ref",
            name="uq_work_objects_assignee_source",
        ),
    )
    op.create_index(
        "ix_work_objects_assignee_fetched",
        "work_objects",
        ["assignee_ai_user_id", "source_fetched_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_objects_assignee_fetched", table_name="work_objects")
    op.drop_table("work_objects")
