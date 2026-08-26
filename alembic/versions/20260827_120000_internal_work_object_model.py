"""Add the Work Object state-authority discriminator.

Revision ID: 20260827_120000
Revises: 20260821_120000
Create Date: 2026-08-27
"""

from __future__ import annotations

import os

import sqlalchemy as sa

from alembic import op

revision = "20260827_120000"
down_revision = "20260821_120000"
branch_labels = None
depends_on = None

_OA_SNAPSHOT_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[object]], ...] = (
    ("source_ref", sa.Text()),
    ("source_title", sa.Text()),
    ("source_status", sa.Text()),
    ("source_received_at", sa.Text()),
    ("source_created_at", sa.Text()),
    ("source_workflow_type_id", sa.Text()),
    ("source_fetched_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.add_column(
        "work_objects",
        sa.Column(
            "state_authority",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'external_snapshot'"),
        ),
    )
    for column_name, existing_type in _OA_SNAPSHOT_COLUMNS:
        op.alter_column(
            "work_objects",
            column_name,
            existing_type=existing_type,
            nullable=True,
        )

    op.drop_constraint(
        "ck_work_objects_source_system",
        "work_objects",
        type_="check",
    )
    op.drop_constraint(
        "ck_work_objects_source_kind",
        "work_objects",
        type_="check",
    )
    op.drop_constraint(
        "uq_work_objects_assignee_source",
        "work_objects",
        type_="unique",
    )

    op.create_check_constraint(
        "ck_work_objects_state_authority",
        "work_objects",
        "state_authority IN ('external_snapshot', 'internal')",
    )
    op.create_check_constraint(
        "ck_work_objects_external_snapshot_fields",
        "work_objects",
        "state_authority <> 'external_snapshot' OR "
        "(source_ref IS NOT NULL AND source_title IS NOT NULL "
        "AND source_status IS NOT NULL AND source_received_at IS NOT NULL "
        "AND source_created_at IS NOT NULL "
        "AND source_workflow_type_id IS NOT NULL "
        "AND source_fetched_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_work_objects_internal_fields",
        "work_objects",
        "state_authority <> 'internal' OR "
        "(source_ref IS NULL AND source_title IS NULL "
        "AND source_status IS NULL AND source_received_at IS NULL "
        "AND source_created_at IS NULL "
        "AND source_workflow_type_id IS NULL "
        "AND source_fetched_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_work_objects_source_system",
        "work_objects",
        "state_authority <> 'external_snapshot' OR source_system = 'oa'",
    )
    op.create_check_constraint(
        "ck_work_objects_source_kind",
        "work_objects",
        "state_authority <> 'external_snapshot' "
        "OR source_kind = 'pending_workflow'",
    )
    op.create_index(
        "uq_work_objects_assignee_source",
        "work_objects",
        ["assignee_ai_user_id", "source_system", "source_ref"],
        unique=True,
        postgresql_where=sa.text("state_authority = 'external_snapshot'"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_internal_rows = bool(
        connection.execute(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM work_objects "
                "WHERE state_authority = 'internal'"
                ")"
            )
        ).scalar_one()
    )
    allow_destructive = os.environ.get("ALLOW_DESTRUCTIVE_DOWNGRADE") == "1"
    if has_internal_rows and not allow_destructive:
        raise RuntimeError(
            "表中存在内部任务数据，结构回滚将删除它们；确认后设置 "
            "ALLOW_DESTRUCTIVE_DOWNGRADE=1 重试"
        )
    if has_internal_rows:
        connection.execute(
            sa.text(
                "DELETE FROM work_objects WHERE state_authority = 'internal'"
            )
        )

    op.drop_index("uq_work_objects_assignee_source", table_name="work_objects")
    for constraint_name in (
        "ck_work_objects_source_kind",
        "ck_work_objects_source_system",
        "ck_work_objects_internal_fields",
        "ck_work_objects_external_snapshot_fields",
        "ck_work_objects_state_authority",
    ):
        op.drop_constraint(constraint_name, "work_objects", type_="check")

    for column_name, existing_type in _OA_SNAPSHOT_COLUMNS:
        op.alter_column(
            "work_objects",
            column_name,
            existing_type=existing_type,
            nullable=False,
        )
    op.create_check_constraint(
        "ck_work_objects_source_system",
        "work_objects",
        "source_system = 'oa'",
    )
    op.create_check_constraint(
        "ck_work_objects_source_kind",
        "work_objects",
        "source_kind = 'pending_workflow'",
    )
    op.create_unique_constraint(
        "uq_work_objects_assignee_source",
        "work_objects",
        ["assignee_ai_user_id", "source_system", "source_ref"],
    )
    op.drop_column("work_objects", "state_authority")
