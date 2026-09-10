"""Persist internal dispatches and atomic idempotency receipts.

Historical rows keep NULL dispatch fields without backfills. A populated dispatch
cannot be downgraded: the operator must retain this schema and its data.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260910_120000"
down_revision = "20260909_120000"
branch_labels = None
depends_on = None

_TEXT_COLUMNS = (
    "tenant_id",
    "owner_department_id",
    "initiator_ai_user_id",
    "target_kind",
    "assignee_directory_user_id",
    "title",
    "kind",
    "requirement",
    "receipt_requirement",
    "status",
)
_NEW_COLUMNS = (*_TEXT_COLUMNS, "reminder_choices", "version")
_MANUAL_CHECK = """
(source_kind <> 'manual_dispatch' OR (
  state_authority = 'internal' AND source_system = 'eternalai'
  AND tenant_id IS NOT NULL AND char_length(tenant_id) > 0
  AND owner_department_id IS NOT NULL AND char_length(owner_department_id) BETWEEN 1 AND 128
  AND initiator_ai_user_id IS NOT NULL AND char_length(initiator_ai_user_id) > 0
  AND version IS NOT NULL AND version >= 1
  AND title IS NOT NULL AND char_length(title) BETWEEN 1 AND 200
  AND kind IS NOT NULL AND kind IN ('通知','督办令','工作任务','提醒')
  AND requirement IS NOT NULL AND char_length(requirement) <= 10000
  AND receipt_requirement IS NOT NULL AND char_length(receipt_requirement) <= 2000
  AND target_kind IS NOT NULL AND target_kind IN ('user','department')
  AND status IS NOT NULL AND status IN ('assigned','department_pending')
  AND assignee_ai_user_id IS NULL
  AND ((target_kind = 'user' AND assignee_directory_user_id IS NOT NULL
        AND char_length(assignee_directory_user_id) BETWEEN 1 AND 128
        AND assignee_display_name IS NULL AND status = 'assigned')
    OR (target_kind = 'department' AND assignee_directory_user_id IS NULL
        AND assignee_display_name IS NOT NULL AND status = 'department_pending'))
  AND reminder_choices IS NOT NULL AND jsonb_typeof(reminder_choices) = 'array'
  AND reminder_choices <@ '["提前 7 天","提前 3 天","提前 1 天","逾期当天"]'::jsonb
  AND reminder_choices = (
    CASE WHEN reminder_choices @> '["提前 7 天"]'::jsonb
      THEN '["提前 7 天"]'::jsonb ELSE '[]'::jsonb END ||
    CASE WHEN reminder_choices @> '["提前 3 天"]'::jsonb
      THEN '["提前 3 天"]'::jsonb ELSE '[]'::jsonb END ||
    CASE WHEN reminder_choices @> '["提前 1 天"]'::jsonb
      THEN '["提前 1 天"]'::jsonb ELSE '[]'::jsonb END ||
    CASE WHEN reminder_choices @> '["逾期当天"]'::jsonb
      THEN '["逾期当天"]'::jsonb ELSE '[]'::jsonb END)
  AND (due_at IS NOT NULL OR reminder_choices = '[]'::jsonb)
  AND created_at IS NOT NULL AND updated_at IS NOT NULL
  AND handling_mark IS NULL AND handling_marked_by_ai_user_id IS NULL
  AND handling_marked_at IS NULL AND task_record_id IS NULL
)) IS TRUE
"""


def upgrade() -> None:
    for name in _TEXT_COLUMNS:
        op.add_column("work_objects", sa.Column(name, sa.Text(), nullable=True))
    op.add_column("work_objects", sa.Column("reminder_choices", postgresql.JSONB(), nullable=True))
    op.add_column("work_objects", sa.Column("version", sa.BigInteger(), nullable=True))
    for name in ("assignee_ai_user_id", "assignee_display_name"):
        op.alter_column("work_objects", name, existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        "ck_work_objects_manual_dispatch_complete",
        "work_objects",
        _MANUAL_CHECK,
    )
    legacy_fields = (
        "assignee_ai_user_id IS NOT NULL AND assignee_display_name IS NOT NULL AND "
        + " AND ".join(f"{name} IS NULL" for name in _NEW_COLUMNS)
    )
    op.create_check_constraint(
        "ck_work_objects_external_dispatch_fields",
        "work_objects",
        "(state_authority <> 'external_snapshot' OR (" + legacy_fields + ")) IS TRUE",
    )
    op.create_check_constraint(
        "ck_work_objects_legacy_internal_dispatch_fields",
        "work_objects",
        "(state_authority <> 'internal' OR source_kind = 'manual_dispatch' OR ("
        + legacy_fields
        + ")) IS TRUE",
    )
    for suffix, name in (
        ("department", "owner_department_id"),
        ("initiator", "initiator_ai_user_id"),
    ):
        op.create_index(
            "ix_work_objects_dispatch_" + suffix,
            "work_objects",
            ["tenant_id", name],
            postgresql_where=sa.text(
                "state_authority = 'internal' AND source_kind = 'manual_dispatch' "
                "AND version IS NOT NULL"
            ),
        )
    op.create_table(
        "work_object_dispatch_receipts",
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("initiator_ai_user_id", sa.Text(), primary_key=True),
        sa.Column("operation", sa.Text(), primary_key=True),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("authorization_summary", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("operation = 'dispatch'", name="ck_dispatch_receipt_operation"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    populated = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM work_objects WHERE source_kind = 'manual_dispatch') "
            "OR EXISTS (SELECT 1 FROM work_object_dispatch_receipts)"
        )
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "Internal dispatch data exists; downgrade is refused without data changes."
        )
    op.drop_table("work_object_dispatch_receipts")
    for suffix in ("department", "initiator"):
        op.drop_index("ix_work_objects_dispatch_" + suffix, table_name="work_objects")
    for name in (
        "ck_work_objects_manual_dispatch_complete",
        "ck_work_objects_external_dispatch_fields",
        "ck_work_objects_legacy_internal_dispatch_fields",
    ):
        op.drop_constraint(name, "work_objects", type_="check")
    for name in ("assignee_ai_user_id", "assignee_display_name"):
        op.alter_column("work_objects", name, existing_type=sa.Text(), nullable=False)
    for name in reversed(_NEW_COLUMNS):
        op.drop_column("work_objects", name)
