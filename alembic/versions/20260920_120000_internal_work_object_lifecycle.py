"""Persist internal lifecycle evidence without rewriting published receipts."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260920_120000"
down_revision = "20260915_120000"
branch_labels = None
depends_on = None

_INITIAL_CHECK = (
    '\n'
    "(source_kind <> 'manual_dispatch' OR (\n"
    "  state_authority = 'internal' AND source_system = 'eternalai'\n"
    '  AND tenant_id IS NOT NULL AND char_length(tenant_id) > 0\n'
    '  AND owner_department_id IS NOT NULL AND char_length(owner_department_id) '
    'BETWEEN 1 AND 128\n'
    '  AND initiator_ai_user_id IS NOT NULL AND '
    'char_length(initiator_ai_user_id) > 0\n'
    '  AND version IS NOT NULL AND version >= 1\n'
    '  AND title IS NOT NULL AND char_length(title) BETWEEN 1 AND 200\n'
    "  AND kind IS NOT NULL AND kind IN ('通知','督办令','工作任务','提醒')\n"
    '  AND requirement IS NOT NULL AND char_length(requirement) <= 10000\n'
    '  AND receipt_requirement IS NOT NULL AND char_length(receipt_requirement) '
    '<= 2000\n'
    "  AND target_kind IS NOT NULL AND target_kind IN ('user','department')\n"
    "  AND status IS NOT NULL AND status IN ('assigned','department_pending')\n"
    '  AND assignee_ai_user_id IS NULL\n'
    "  AND ((target_kind = 'user' AND assignee_directory_user_id IS NOT NULL\n"
    '        AND char_length(assignee_directory_user_id) BETWEEN 1 AND 128\n'
    "        AND assignee_display_name IS NULL AND status = 'assigned')\n"
    "    OR (target_kind = 'department' AND assignee_directory_user_id IS NULL\n"
    '        AND assignee_display_name IS NOT NULL AND status = '
    "'department_pending'))\n"
    '  AND reminder_choices IS NOT NULL AND jsonb_typeof(reminder_choices) = '
    "'array'\n"
    '  AND reminder_choices <@ \'["提前 7 天","提前 3 天","提前 1 天","逾期当天"]\'::jsonb\n'
    '  AND reminder_choices = (\n'
    '    CASE WHEN reminder_choices @> \'["提前 7 天"]\'::jsonb\n'
    '      THEN \'["提前 7 天"]\'::jsonb ELSE \'[]\'::jsonb END ||\n'
    '    CASE WHEN reminder_choices @> \'["提前 3 天"]\'::jsonb\n'
    '      THEN \'["提前 3 天"]\'::jsonb ELSE \'[]\'::jsonb END ||\n'
    '    CASE WHEN reminder_choices @> \'["提前 1 天"]\'::jsonb\n'
    '      THEN \'["提前 1 天"]\'::jsonb ELSE \'[]\'::jsonb END ||\n'
    '    CASE WHEN reminder_choices @> \'["逾期当天"]\'::jsonb\n'
    '      THEN \'["逾期当天"]\'::jsonb ELSE \'[]\'::jsonb END)\n'
    "  AND (due_at IS NOT NULL OR reminder_choices = '[]'::jsonb)\n"
    '  AND created_at IS NOT NULL AND updated_at IS NOT NULL\n'
    '  AND handling_mark IS NULL AND handling_marked_by_ai_user_id IS NULL\n'
    '  AND handling_marked_at IS NULL AND task_record_id IS NULL\n'
    ')) IS TRUE\n'
)
_MANUAL_CHECK = (
    '(\n'
    "    source_kind <> 'manual_dispatch' OR (\n"
    "      state_authority = 'internal' AND source_system = 'eternalai'\n"
    '      AND tenant_id IS NOT NULL AND char_length(tenant_id) > 0\n'
    '      AND owner_department_id IS NOT NULL AND '
    'char_length(owner_department_id) BETWEEN 1 AND 128\n'
    '      AND initiator_ai_user_id IS NOT NULL AND '
    'char_length(initiator_ai_user_id) > 0\n'
    '      AND version IS NOT NULL AND version >= 1\n'
    '      AND created_at IS NOT NULL AND updated_at IS NOT NULL\n'
    '      AND title IS NOT NULL AND char_length(title) BETWEEN 1 AND 200\n'
    "      AND kind IS NOT NULL AND kind IN ('通知','督办令','工作任务','提醒')\n"
    '      AND requirement IS NOT NULL AND char_length(requirement) <= 10000\n'
    '      AND receipt_requirement IS NOT NULL AND '
    'char_length(receipt_requirement) <= 2000\n'
    "      AND target_kind IS NOT NULL AND target_kind IN ('user','department')\n"
    '      AND status IS NOT NULL AND status IN '
    "('assigned','department_pending','in_progress','completed')\n"
    '      AND assignee_ai_user_id IS NULL\n'
    "      AND ((target_kind = 'user' AND assignee_directory_user_id IS NOT NULL\n"
    '            AND char_length(assignee_directory_user_id) BETWEEN 1 AND 128\n'
    '            AND assignee_display_name IS NULL AND status <> '
    "'department_pending')\n"
    "        OR (target_kind = 'department' AND assignee_directory_user_id IS NULL\n"
    "            AND assignee_display_name IS NOT NULL AND status <> 'assigned'))\n"
    '      AND reminder_choices IS NOT NULL AND jsonb_typeof(reminder_choices) '
    "= 'array'\n"
    '      AND reminder_choices <@ \'["提前 7 天","提前 3 天","提前 1 天","逾期当天"]\'::jsonb\n'
    '      AND reminder_choices = (\n'
    '        CASE WHEN reminder_choices @> \'["提前 7 天"]\'::jsonb THEN \'["提前 7 '
    '天"]\'::jsonb ELSE \'[]\'::jsonb END ||\n'
    '        CASE WHEN reminder_choices @> \'["提前 3 天"]\'::jsonb THEN \'["提前 3 '
    '天"]\'::jsonb ELSE \'[]\'::jsonb END ||\n'
    '        CASE WHEN reminder_choices @> \'["提前 1 天"]\'::jsonb THEN \'["提前 1 '
    '天"]\'::jsonb ELSE \'[]\'::jsonb END ||\n'
    '        CASE WHEN reminder_choices @> \'["逾期当天"]\'::jsonb THEN '
    '\'["逾期当天"]\'::jsonb ELSE \'[]\'::jsonb END)\n'
    "      AND (due_at IS NOT NULL OR reminder_choices = '[]'::jsonb)\n"
    '      AND handling_mark IS NULL AND handling_marked_by_ai_user_id IS NULL\n'
    '      AND handling_marked_at IS NULL AND task_record_id IS NULL)) IS TRUE'
)
_LIFECYCLE_CHECK = (
    '(\n'
    "    (source_kind <> 'manual_dispatch' AND accepted_by_ai_user_id IS NULL "
    'AND accepted_at IS NULL\n'
    '      AND completed_by_ai_user_id IS NULL AND completed_at IS NULL)\n'
    "    OR (state_authority = 'internal' AND source_kind = 'manual_dispatch' "
    'AND (\n'
    "      (status IN ('assigned','department_pending') AND "
    'accepted_by_ai_user_id IS NULL\n'
    '        AND accepted_at IS NULL AND completed_by_ai_user_id IS NULL AND '
    'completed_at IS NULL)\n'
    "      OR (status = 'in_progress' AND version >= 2 AND "
    'accepted_by_ai_user_id IS NOT NULL\n'
    '        AND char_length(accepted_by_ai_user_id) > 0 AND accepted_at IS NOT '
    'NULL\n'
    '        AND accepted_at >= created_at AND updated_at >= accepted_at\n'
    '        AND completed_by_ai_user_id IS NULL AND completed_at IS NULL)\n'
    "      OR (status = 'completed' AND version >= 3 AND accepted_by_ai_user_id "
    'IS NOT NULL\n'
    '        AND char_length(accepted_by_ai_user_id) > 0 AND accepted_at IS NOT '
    'NULL\n'
    '        AND completed_by_ai_user_id IS NOT NULL AND '
    'completed_by_ai_user_id = accepted_by_ai_user_id\n'
    '        AND completed_at IS NOT NULL AND accepted_at >= created_at\n'
    '        AND completed_at >= accepted_at AND updated_at = completed_at)))) '
    'IS TRUE'
)
_EVENTS_SQL = (
    'CREATE TABLE work_object_lifecycle_events (\n'
    '  event_id uuid PRIMARY KEY,\n'
    '  work_object_id text NOT NULL,\n'
    '  tenant_id text NOT NULL CHECK (char_length(tenant_id) > 0),\n'
    '  actor_ai_user_id text NOT NULL CHECK (char_length(actor_ai_user_id) > 0),\n'
    '  operation text NOT NULL CHECK (operation IN '
    "('accept','feedback','complete')),\n"
    '  idempotency_key uuid NOT NULL,\n'
    '  request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '
    "'^[0-9a-f]{64}$'),\n"
    '  from_status text NOT NULL,\n'
    '  to_status text NOT NULL,\n'
    '  result_version bigint NOT NULL CHECK (result_version >= 2),\n'
    '  occurred_at timestamptz NOT NULL,\n'
    '  text text,\n'
    '  CONSTRAINT fk_work_object_lifecycle_owner FOREIGN KEY (work_object_id, '
    'tenant_id)\n'
    '    REFERENCES work_objects (work_object_id, tenant_id) ON DELETE RESTRICT,\n'
    '  CONSTRAINT uq_work_object_lifecycle_version UNIQUE (work_object_id, '
    'result_version),\n'
    '  CONSTRAINT uq_work_object_lifecycle_idempotency\n'
    '    UNIQUE (tenant_id, actor_ai_user_id, work_object_id, idempotency_key),\n'
    '  CONSTRAINT ck_work_object_lifecycle_transition CHECK ((\n'
    "    (operation = 'accept' AND from_status IN "
    "('assigned','department_pending')\n"
    "      AND to_status = 'in_progress' AND text IS NULL)\n"
    "    OR (operation = 'feedback' AND from_status = 'in_progress' AND "
    "to_status = 'in_progress'\n"
    '      AND text IS NOT NULL AND char_length(text) BETWEEN 1 AND 2000)\n'
    "    OR (operation = 'complete' AND from_status = 'in_progress' AND "
    "to_status = 'completed'\n"
    '      AND text IS NOT NULL AND char_length(text) BETWEEN 1 AND 2000)) IS '
    'TRUE)\n'
    ');'
)

_COLUMNS = ("accepted_by_ai_user_id", "accepted_at", "completed_by_ai_user_id", "completed_at")


def upgrade() -> None:
    for name in _COLUMNS:
        kind = sa.DateTime(timezone=True) if name.endswith("_at") else sa.Text()
        op.add_column("work_objects", sa.Column(name, kind, nullable=True))
    op.create_unique_constraint(
        "uq_work_objects_id_tenant", "work_objects", ["work_object_id", "tenant_id"]
    )
    op.drop_constraint("ck_work_objects_manual_dispatch_complete", "work_objects", type_="check")
    op.create_check_constraint(
        "ck_work_objects_manual_dispatch_complete", "work_objects", _MANUAL_CHECK
    )
    op.create_check_constraint("ck_work_objects_lifecycle_fields", "work_objects", _LIFECYCLE_CHECK)
    op.execute(
        "CREATE INDEX ix_work_objects_internal_completed ON work_objects "
        "(tenant_id, completed_at DESC, work_object_id) WHERE state_authority = 'internal' "
        "AND source_kind = 'manual_dispatch' AND status = 'completed'"
    )
    op.execute(_EVENTS_SQL)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE work_objects, work_object_lifecycle_events IN ACCESS EXCLUSIVE MODE")
    )
    populated = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM work_object_lifecycle_events) OR EXISTS ("
            "SELECT 1 FROM work_objects WHERE status IN ('in_progress','completed') OR "
            + " OR ".join(name + " IS NOT NULL" for name in _COLUMNS)
            + ")"
        )
    ).scalar_one()
    if populated:
        raise RuntimeError("Lifecycle data exists; downgrade is refused without data changes.")
    op.drop_table("work_object_lifecycle_events")
    op.drop_index("ix_work_objects_internal_completed", table_name="work_objects")
    op.drop_constraint("ck_work_objects_lifecycle_fields", "work_objects", type_="check")
    op.drop_constraint("ck_work_objects_manual_dispatch_complete", "work_objects", type_="check")
    op.create_check_constraint(
        "ck_work_objects_manual_dispatch_complete", "work_objects", _INITIAL_CHECK
    )
    op.drop_constraint("uq_work_objects_id_tenant", "work_objects", type_="unique")
    for name in reversed(_COLUMNS):
        op.drop_column("work_objects", name)
