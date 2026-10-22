"""Dispatch v2 schema only; execution requires separate environment authorization.

No old business rows are rewritten. Empty-schema downgrade refuses new facts.
The current lead relation replaces the redundant circular root lead FK."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261022_120000"
down_revision = "20261009_120000"
branch_labels = None
depends_on = None

_LEGACY_MANUAL = """(
    source_kind <> 'manual_dispatch' OR (
      state_authority = 'internal' AND source_system = 'eternalai'
      AND tenant_id IS NOT NULL AND char_length(tenant_id) > 0
      AND owner_department_id IS NOT NULL AND char_length(owner_department_id) BETWEEN 1
        AND 128
      AND initiator_ai_user_id IS NOT NULL AND char_length(initiator_ai_user_id) > 0
      AND version IS NOT NULL AND version >= 1
      AND created_at IS NOT NULL AND updated_at IS NOT NULL
      AND title IS NOT NULL AND char_length(title) BETWEEN 1 AND 200
      AND kind IS NOT NULL AND kind IN ('通知','督办令','工作任务','提醒')
      AND requirement IS NOT NULL AND char_length(requirement) <= 10000
      AND receipt_requirement IS NOT NULL AND char_length(receipt_requirement) <= 2000
      AND target_kind IS NOT NULL AND target_kind IN ('user','department')
      AND status IS NOT NULL AND status IN
        ('assigned','department_pending','in_progress','completed')
      AND assignee_ai_user_id IS NULL
      AND ((target_kind = 'user' AND assignee_directory_user_id IS NOT NULL
            AND char_length(assignee_directory_user_id) BETWEEN 1 AND 128
            AND assignee_display_name IS NULL AND status <> 'department_pending')
        OR (target_kind = 'department' AND assignee_directory_user_id IS NULL
            AND assignee_display_name IS NOT NULL AND status <> 'assigned'))
      AND reminder_choices IS NOT NULL AND jsonb_typeof(reminder_choices) = 'array'
      AND reminder_choices <@ '["提前 7 天","提前 3 天","提前 1 天","逾期当天"]'::jsonb
      AND reminder_choices = (
        CASE WHEN reminder_choices @> '["提前 7 天"]'::jsonb THEN '["提前 7 天"]'::jsonb
        ELSE '[]'::jsonb END ||
        CASE WHEN reminder_choices @> '["提前 3 天"]'::jsonb THEN '["提前 3 天"]'::jsonb
        ELSE '[]'::jsonb END ||
        CASE WHEN reminder_choices @> '["提前 1 天"]'::jsonb THEN '["提前 1 天"]'::jsonb
        ELSE '[]'::jsonb END ||
        CASE WHEN reminder_choices @> '["逾期当天"]'::jsonb THEN '["逾期当天"]'::jsonb
        ELSE '[]'::jsonb END)
      AND (due_at IS NOT NULL OR reminder_choices = '[]'::jsonb)
      AND handling_mark IS NULL AND handling_marked_by_ai_user_id IS NULL
      AND handling_marked_at IS NULL AND task_record_id IS NULL)) IS TRUE"""
_LEGACY_LIFECYCLE = """(
    (source_kind <> 'manual_dispatch' AND accepted_by_ai_user_id IS NULL AND accepted_at
        IS NULL
      AND completed_by_ai_user_id IS NULL AND completed_at IS NULL)
    OR (state_authority = 'internal' AND source_kind = 'manual_dispatch' AND (
      (status IN ('assigned','department_pending') AND accepted_by_ai_user_id IS NULL
        AND accepted_at IS NULL AND completed_by_ai_user_id IS NULL AND completed_at IS
        NULL)
      OR (status = 'in_progress' AND version >= 2 AND accepted_by_ai_user_id IS NOT NULL
        AND char_length(accepted_by_ai_user_id) > 0 AND accepted_at IS NOT NULL
        AND accepted_at >= created_at AND updated_at >= accepted_at
        AND completed_by_ai_user_id IS NULL AND completed_at IS NULL)
      OR (status = 'completed' AND version >= 3 AND accepted_by_ai_user_id IS NOT NULL
        AND char_length(accepted_by_ai_user_id) > 0 AND accepted_at IS NOT NULL
        AND completed_by_ai_user_id IS NOT NULL AND completed_by_ai_user_id =
        accepted_by_ai_user_id
        AND completed_at IS NOT NULL AND accepted_at >= created_at
        AND completed_at >= accepted_at AND updated_at = completed_at)))) IS TRUE"""
_V2_MANUAL = """(
    source_kind <> 'manual_dispatch' OR (
      state_authority = 'internal' AND source_system = 'eternalai'
      AND tenant_id IS NOT NULL AND char_length(tenant_id) > 0
      AND owner_department_id IS NOT NULL AND char_length(owner_department_id) BETWEEN 1
        AND 128
      AND initiator_ai_user_id IS NOT NULL AND char_length(initiator_ai_user_id) > 0
      AND version IS NOT NULL AND version >= 1
      AND created_at IS NOT NULL AND updated_at IS NOT NULL
      AND title IS NOT NULL AND char_length(title) BETWEEN 1 AND 200
      AND kind IS NOT NULL AND kind IN ('通知','督办令','工作任务','提醒')
      AND requirement IS NOT NULL AND char_length(requirement) <= 10000
      AND receipt_requirement IS NOT NULL AND char_length(receipt_requirement) <= 2000
      AND target_kind IS NOT NULL AND target_kind IN ('user','department')
      AND status IS NOT NULL AND status IN
        ('assigned','department_pending','in_progress','awaiting_department_review','awaiting_acceptance','completed','withdrawn')
      AND assignee_ai_user_id IS NULL
      AND ((target_kind = 'user' AND assignee_directory_user_id IS NOT NULL
            AND char_length(assignee_directory_user_id) BETWEEN 1 AND 128
            AND assignee_display_name IS NULL AND status <> 'department_pending')
        OR (target_kind = 'department' AND assignee_directory_user_id IS NULL
            AND assignee_display_name IS NOT NULL AND status <> 'assigned'))
      AND reminder_choices IS NOT NULL AND jsonb_typeof(reminder_choices) = 'array'
      AND reminder_choices <@ '["提前 7 天","提前 3 天","提前 1 天","逾期当天"]'::jsonb
      AND reminder_choices = (
        CASE WHEN reminder_choices @> '["提前 7 天"]'::jsonb THEN '["提前 7 天"]'::jsonb
        ELSE '[]'::jsonb END ||
        CASE WHEN reminder_choices @> '["提前 3 天"]'::jsonb THEN '["提前 3 天"]'::jsonb
        ELSE '[]'::jsonb END ||
        CASE WHEN reminder_choices @> '["提前 1 天"]'::jsonb THEN '["提前 1 天"]'::jsonb
        ELSE '[]'::jsonb END ||
        CASE WHEN reminder_choices @> '["逾期当天"]'::jsonb THEN '["逾期当天"]'::jsonb
        ELSE '[]'::jsonb END)
      AND (due_at IS NOT NULL OR reminder_choices = '[]'::jsonb)
      AND handling_mark IS NULL AND handling_marked_by_ai_user_id IS NULL
      AND handling_marked_at IS NULL AND task_record_id IS NULL)) IS TRUE"""

_ROOT_COLUMNS = (
    "dispatch_model_version",
    "dispatch_mode",
    "completion_mode",
    "withdrawn_at",
    "withdrawn_by_ai_user_id",
)
_EVENT_COLUMNS = (
    "event_model_version",
    "handling_relation_id",
    "relation_from_status",
    "relation_to_status",
    "payload",
    "visibility",
)
_TABLES = (
    "work_object_handling_relations",
    "work_object_remarks",
    "work_object_remark_history",
    "work_object_attachments",
    "work_object_series",
    "work_object_series_versions",
    "work_object_series_occurrences",
    "work_object_series_occurrence_items",
)


def upgrade() -> None:
    _execute_statements("""
      ALTER TABLE work_objects
        ADD COLUMN dispatch_model_version SMALLINT,
        ADD COLUMN dispatch_mode TEXT,
        ADD COLUMN completion_mode TEXT,
        ADD COLUMN withdrawn_at TIMESTAMPTZ,
        ADD COLUMN withdrawn_by_ai_user_id TEXT;
      ALTER TABLE work_object_lifecycle_events
        ADD COLUMN event_model_version SMALLINT,
        ADD COLUMN handling_relation_id UUID,
        ADD COLUMN relation_from_status TEXT,
        ADD COLUMN relation_to_status TEXT,
        ADD COLUMN payload JSONB,
        ADD COLUMN visibility TEXT;
    """)
    op.drop_constraint("ck_work_objects_manual_dispatch_complete", "work_objects", type_="check")
    op.create_check_constraint(
        "ck_work_objects_manual_dispatch_complete",
        "work_objects",
        "((dispatch_model_version IS NULL AND " + _LEGACY_MANUAL + ") OR "
        "(dispatch_model_version=2 AND source_kind='manual_dispatch' AND "
        + _V2_MANUAL
        + ")) IS TRUE",
    )
    op.drop_constraint("ck_work_objects_lifecycle_fields", "work_objects", type_="check")
    op.create_check_constraint(
        "ck_work_objects_lifecycle_fields",
        "work_objects",
        """(
      (dispatch_model_version IS NULL AND """
        + _LEGACY_LIFECYCLE
        + (
            """) OR
      (dispatch_model_version=2 AND
       ((status='completed' AND completed_by_ai_user_id IS NOT NULL AND
        char_length(completed_by_ai_user_id)>0
          AND completed_at IS NOT NULL AND completed_at>=created_at AND
        updated_at=completed_at)
        OR (status<>'completed' AND completed_by_ai_user_id IS NULL AND completed_at IS
        NULL))
       AND ((accepted_at IS NULL AND accepted_by_ai_user_id IS NULL)
         OR (accepted_at IS NOT NULL AND accepted_by_ai_user_id IS NOT NULL
             AND accepted_at>=created_at AND updated_at>=accepted_at))
       AND ((status='withdrawn' AND withdrawn_at IS NOT NULL AND withdrawn_by_ai_user_id
        IS NOT NULL
             AND char_length(withdrawn_by_ai_user_id)>0 AND withdrawn_at>=created_at AND
        updated_at=withdrawn_at)
         OR (status<>'withdrawn' AND withdrawn_at IS NULL AND withdrawn_by_ai_user_id IS
        NULL)))) IS TRUE"""
        ),
    )
    op.create_check_constraint(
        "ck_work_objects_dispatch_v2_modes",
        "work_objects",
        (
            """(
      (dispatch_model_version IS NULL AND dispatch_mode IS NULL AND completion_mode IS
        NULL
        AND withdrawn_at IS NULL AND withdrawn_by_ai_user_id IS NULL)
      OR (dispatch_model_version=2 AND state_authority='internal' AND
        source_kind='manual_dispatch'
        AND dispatch_mode IN ('independent','collaborative')
        AND completion_mode IN ('submit_completes','confirm_required'))) IS TRUE"""
        ),
    )
    _execute_statements(
        (
            """
      CREATE INDEX ix_work_objects_sent_status ON work_objects
        (tenant_id,initiator_ai_user_id,status,updated_at,work_object_id) WHERE
        dispatch_model_version=2;
      CREATE INDEX ix_work_objects_withdrawn ON work_objects
        (tenant_id,withdrawn_at DESC,work_object_id) WHERE status='withdrawn';
      CREATE TABLE work_object_handling_relations (
        relation_id UUID PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        work_object_id TEXT NOT NULL,
        target_kind TEXT NOT NULL CHECK(target_kind IN ('user','department')),
        target_department_id TEXT NOT NULL CHECK(char_length(target_department_id)
        BETWEEN 1 AND 128),
        target_directory_user_id TEXT,
        role TEXT NOT NULL CHECK(role IN ('single','lead','collaborator')),
        assignee_directory_user_id TEXT,
        assignee_ai_user_id TEXT,
        status TEXT NOT NULL CHECK(status IN
        ('department_pending','assigned','in_progress','awaiting_department_review','submitted')),
        accepted_at TIMESTAMPTZ,
        accepted_by_ai_user_id TEXT,
        submitted_at TIMESTAMPTZ,
        submitted_by_ai_user_id TEXT,
        submitted_result TEXT,
        reviewed_at TIMESTAMPTZ,
        reviewed_by_ai_user_id TEXT,
        review_result BOOLEAN,
        review_reason TEXT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        superseded_at TIMESTAMPTZ,
        UNIQUE(tenant_id,work_object_id,relation_id),
        FOREIGN KEY(work_object_id,tenant_id) REFERENCES
        work_objects(work_object_id,tenant_id) ON DELETE RESTRICT,
        CHECK((target_kind='user' AND target_directory_user_id IS NOT NULL AND
        char_length(target_directory_user_id) BETWEEN 1 AND 128)
           OR (target_kind='department' AND target_directory_user_id IS NULL)),
        CHECK(updated_at>=created_at AND (superseded_at IS NULL OR
        superseded_at>=created_at)),
        CHECK((status='department_pending' AND target_kind='department' AND
        assignee_directory_user_id IS NULL)
           OR (status<>'department_pending' AND assignee_directory_user_id IS NOT
        NULL)),
        CHECK((accepted_at IS NULL AND accepted_by_ai_user_id IS NULL)
           OR (accepted_at IS NOT NULL AND accepted_by_ai_user_id IS NOT NULL AND
        accepted_at>=created_at)),
        CHECK((status IN ('awaiting_department_review','submitted') AND submitted_at IS
        NOT NULL
                AND submitted_by_ai_user_id IS NOT NULL AND submitted_result IS NOT NULL
                AND char_length(submitted_result) BETWEEN 1 AND 10000)
           OR (status NOT IN ('awaiting_department_review','submitted') AND submitted_at
        IS NULL
                AND submitted_by_ai_user_id IS NULL AND submitted_result IS NULL)),
        CHECK((reviewed_at IS NULL AND reviewed_by_ai_user_id IS NULL AND review_result
        IS NULL AND review_reason IS NULL)
           OR (reviewed_at IS NOT NULL AND reviewed_by_ai_user_id IS NOT NULL AND
        review_result IS NOT NULL
               AND target_kind='department' AND (review_result OR
        char_length(btrim(review_reason))>0)))
      );
      CREATE UNIQUE INDEX uq_work_relation_current_target ON
        work_object_handling_relations
        (tenant_id,work_object_id,target_kind,target_department_id,
        COALESCE(target_directory_user_id,'')) WHERE superseded_at IS NULL;
      CREATE UNIQUE INDEX uq_work_relation_current_lead ON
        work_object_handling_relations
        (tenant_id,work_object_id) WHERE superseded_at IS NULL AND role='lead';
      CREATE INDEX ix_work_relation_assignee ON work_object_handling_relations
        (tenant_id,assignee_directory_user_id,assignee_ai_user_id) WHERE superseded_at
        IS NULL;
      CREATE INDEX ix_work_relation_department ON work_object_handling_relations
        (tenant_id,target_department_id,status) WHERE superseded_at IS NULL;
      CREATE TABLE work_object_remarks (
        tenant_id TEXT NOT NULL,work_object_id TEXT NOT NULL,text TEXT NOT NULL
        CHECK(char_length(text)<=2000),
        version BIGINT NOT NULL CHECK(version>=1),updated_by TEXT NOT NULL,updated_at
        TIMESTAMPTZ NOT NULL,
        PRIMARY KEY(tenant_id,work_object_id),
        FOREIGN KEY(work_object_id,tenant_id) REFERENCES
        work_objects(work_object_id,tenant_id) ON DELETE RESTRICT
      );
      CREATE TABLE work_object_remark_history (
        tenant_id TEXT NOT NULL,work_object_id TEXT NOT NULL,remark_version BIGINT NOT
        NULL CHECK(remark_version>=1),
        text TEXT NOT NULL CHECK(char_length(text)<=2000),actor_ai_user_id TEXT NOT
        NULL,occurred_at TIMESTAMPTZ NOT NULL,
        idempotency_key UUID NOT NULL,request_fingerprint TEXT NOT NULL
        CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),
        PRIMARY KEY(tenant_id,work_object_id,remark_version),
        UNIQUE(tenant_id,actor_ai_user_id,work_object_id,idempotency_key),
        FOREIGN KEY(work_object_id,tenant_id) REFERENCES
        work_objects(work_object_id,tenant_id) ON DELETE RESTRICT
      );
      CREATE TABLE work_object_attachments (
        attachment_id UUID PRIMARY KEY,tenant_id TEXT NOT NULL,owner_ai_user_id TEXT NOT
        NULL,
        work_object_id TEXT,handling_relation_id UUID,
        purpose TEXT NOT NULL CHECK(purpose IN ('task_material','result')),
        visibility TEXT NOT NULL CHECK(visibility IN ('shared','department_internal')),
        filename TEXT NOT NULL CHECK(char_length(filename) BETWEEN 1 AND 255),media_type
        TEXT NOT NULL,
        size_bytes BIGINT NOT NULL CHECK(size_bytes>0 AND size_bytes<=20971520),
        sha256 TEXT NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),payload BYTEA NOT NULL,
        version BIGINT NOT NULL DEFAULT 1 CHECK(version>=1),created_at TIMESTAMPTZ NOT
        NULL,
        linked_at TIMESTAMPTZ,detached_at TIMESTAMPTZ,idempotency_key UUID NOT NULL,
        request_fingerprint TEXT NOT NULL CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),
        UNIQUE(tenant_id,attachment_id),UNIQUE(tenant_id,owner_ai_user_id,idempotency_key),
        FOREIGN KEY(work_object_id,tenant_id) REFERENCES
        work_objects(work_object_id,tenant_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,work_object_id,handling_relation_id) REFERENCES
          work_object_handling_relations(tenant_id,work_object_id,relation_id) ON DELETE
        RESTRICT,
        CHECK(size_bytes=octet_length(payload)),
        CHECK((work_object_id IS NULL AND handling_relation_id IS NULL AND linked_at IS
        NULL AND detached_at IS NULL)
          OR (work_object_id IS NOT NULL AND linked_at IS NOT NULL)),
        CHECK(visibility<>'department_internal' OR handling_relation_id IS NOT NULL OR
        work_object_id IS NULL)
      );
      CREATE INDEX ix_work_attachment_task ON
        work_object_attachments(tenant_id,work_object_id);
      CREATE INDEX ix_work_attachment_owner ON
        work_object_attachments(tenant_id,owner_ai_user_id);
      CREATE TABLE work_object_series (
        series_id UUID PRIMARY KEY,tenant_id TEXT NOT NULL,creator_ai_user_id TEXT NOT
        NULL CHECK(char_length(creator_ai_user_id)>0),
        creator_directory_user_id TEXT NOT NULL
        CHECK(char_length(creator_directory_user_id)>0),
        enabled BOOLEAN NOT NULL,current_version BIGINT NOT NULL
        CHECK(current_version>=1),
        weekdays JSONB NOT NULL CHECK(jsonb_typeof(weekdays)='array' AND
        jsonb_array_length(weekdays) BETWEEN 1 AND 7
          AND weekdays <@ '[1,2,3,4,5,6,7]'::jsonb
          AND weekdays=(CASE WHEN weekdays @> '[1]'::jsonb THEN '[1]'::jsonb ELSE
        '[]'::jsonb END ||
            CASE WHEN weekdays @> '[2]'::jsonb THEN '[2]'::jsonb ELSE '[]'::jsonb END ||
            CASE WHEN weekdays @> '[3]'::jsonb THEN '[3]'::jsonb ELSE '[]'::jsonb END ||
            CASE WHEN weekdays @> '[4]'::jsonb THEN '[4]'::jsonb ELSE '[]'::jsonb END ||
            CASE WHEN weekdays @> '[5]'::jsonb THEN '[5]'::jsonb ELSE '[]'::jsonb END ||
            CASE WHEN weekdays @> '[6]'::jsonb THEN '[6]'::jsonb ELSE '[]'::jsonb END ||
            CASE WHEN weekdays @> '[7]'::jsonb THEN '[7]'::jsonb ELSE '[]'::jsonb END)),
        local_time TIME NOT NULL,timezone TEXT NOT NULL,start_date DATE NOT NULL,
        deadline_spec JSONB NOT NULL CHECK(jsonb_typeof(deadline_spec)='object'
          AND deadline_spec ? 'days_after' AND deadline_spec ? 'local_time'
          AND jsonb_typeof(deadline_spec->'days_after')='number' AND
        (deadline_spec->>'days_after')::numeric>=0),
        template JSONB NOT NULL CHECK(jsonb_typeof(template)='object'),
        next_due_at TIMESTAMPTZ,created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ
        NOT NULL,
        UNIQUE(tenant_id,series_id),CHECK(NOT enabled OR next_due_at IS NOT NULL)
      );
      CREATE INDEX ix_work_series_due ON work_object_series(enabled,next_due_at);
      CREATE TABLE work_object_series_versions (
        tenant_id TEXT NOT NULL,series_id UUID NOT NULL,version BIGINT NOT NULL
        CHECK(version>=1),
        operation TEXT NOT NULL CHECK(operation IN
        ('create','update','enable','disable')),
        actor_ai_user_id TEXT NOT NULL,occurred_at TIMESTAMPTZ NOT NULL,snapshot JSONB
        NOT NULL,
        idempotency_key UUID NOT NULL,request_fingerprint TEXT NOT NULL
        CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),
        PRIMARY
        KEY(tenant_id,series_id,version),UNIQUE(tenant_id,series_id,actor_ai_user_id,idempotency_key),
        FOREIGN KEY(tenant_id,series_id) REFERENCES
        work_object_series(tenant_id,series_id) ON DELETE RESTRICT
      );
      CREATE TABLE work_object_series_occurrences (
        occurrence_id UUID PRIMARY KEY,tenant_id TEXT NOT NULL,series_id UUID NOT NULL,
        scheduled_at TIMESTAMPTZ NOT NULL,series_version BIGINT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('generated','blocked')),blocked_code
        TEXT,generated_at TIMESTAMPTZ,
        UNIQUE(tenant_id,occurrence_id),UNIQUE(tenant_id,series_id,scheduled_at),
        FOREIGN KEY(tenant_id,series_id,series_version) REFERENCES
        work_object_series_versions(tenant_id,series_id,version) ON DELETE RESTRICT,
        CHECK((state='generated' AND generated_at IS NOT NULL AND blocked_code IS NULL)
          OR (state='blocked' AND generated_at IS NULL AND blocked_code IS NOT NULL AND
        char_length(blocked_code)>0))
      );
      CREATE TABLE work_object_series_occurrence_items (
        tenant_id TEXT NOT NULL,occurrence_id UUID NOT NULL,work_object_id TEXT NOT
        NULL,
        PRIMARY
        KEY(tenant_id,occurrence_id,work_object_id),UNIQUE(tenant_id,work_object_id),
        FOREIGN KEY(tenant_id,occurrence_id) REFERENCES
        work_object_series_occurrences(tenant_id,occurrence_id) ON DELETE RESTRICT,
        FOREIGN KEY(work_object_id,tenant_id) REFERENCES
        work_objects(work_object_id,tenant_id) ON DELETE RESTRICT
      );
      ALTER TABLE work_object_lifecycle_events ADD CONSTRAINT fk_work_event_relation
        FOREIGN KEY(tenant_id,work_object_id,handling_relation_id) REFERENCES
          work_object_handling_relations(tenant_id,work_object_id,relation_id) ON DELETE
        RESTRICT;
    """
        )
    )
    _event_constraints(upgrade=True)


def _event_constraints(*, upgrade: bool) -> None:
    op.drop_constraint(
        "work_object_lifecycle_events_operation_check",
        "work_object_lifecycle_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_work_object_lifecycle_transition", "work_object_lifecycle_events", type_="check"
    )
    legacy = """((operation='accept' AND from_status IN ('assigned','department_pending') AND
        to_status='in_progress' AND text IS NULL)
       OR (operation='feedback' AND from_status='in_progress' AND
        to_status='in_progress' AND text IS NOT NULL AND char_length(text) BETWEEN 1 AND
        2000)
       OR (operation='complete' AND from_status='in_progress' AND to_status='completed'
        AND text IS NOT NULL AND char_length(text) BETWEEN 1 AND 2000)) IS TRUE"""
    if not upgrade:
        op.create_check_constraint(
            "work_object_lifecycle_events_operation_check",
            "work_object_lifecycle_events",
            "operation IN ('accept','feedback','complete')",
        )
        op.create_check_constraint(
            "ck_work_object_lifecycle_transition", "work_object_lifecycle_events", legacy
        )
        return
    op.create_check_constraint(
        "work_object_lifecycle_events_operation_check",
        "work_object_lifecycle_events",
        (
            "operation IN ('assign','accept','feedback','submit','complete','department_review'"
            ",'submit_summary','confirm','return','revise','withdraw','extend','reassign')"
        ),
    )
    op.create_check_constraint(
        "ck_work_object_lifecycle_transition",
        "work_object_lifecycle_events",
        (
            """(
      (event_model_version IS NULL AND handling_relation_id IS NULL AND
        relation_from_status IS NULL
       AND relation_to_status IS NULL AND payload IS NULL AND visibility IS NULL AND ("""
        )
        + legacy
        + (
            """))
      OR (event_model_version=2 AND visibility IN ('shared','department_internal')
        AND jsonb_typeof(payload)='object'
        AND ((handling_relation_id IS NULL AND relation_from_status IS NULL AND
        relation_to_status IS NULL AND visibility='shared')
          OR (handling_relation_id IS NOT NULL
            AND relation_from_status IN
        ('department_pending','assigned','in_progress','awaiting_department_review','submitted')
            AND relation_to_status IN
        ('department_pending','assigned','in_progress','awaiting_department_review','submitted')))
        AND from_status IN
        ('assigned','department_pending','in_progress','awaiting_department_review','awaiting_acceptance')
        AND to_status IN
        ('assigned','department_pending','in_progress','awaiting_department_review','awaiting_acceptance','completed','withdrawn')
        AND (
          (operation='assign' AND handling_relation_id IS NOT NULL AND
        relation_to_status='assigned'
            AND to_status IN ('assigned','in_progress','awaiting_department_review') AND
        text IS NULL)
          OR (operation='accept' AND handling_relation_id IS NOT NULL AND
        relation_from_status='assigned'
            AND relation_to_status='in_progress' AND to_status IN
        ('in_progress','awaiting_department_review') AND text IS NULL)
          OR (operation='feedback' AND handling_relation_id IS NOT NULL AND
        relation_from_status='in_progress'
            AND relation_to_status='in_progress' AND to_status IN
        ('in_progress','awaiting_department_review') AND char_length(text) BETWEEN 1 AND
        10000)
          OR (operation IN ('submit','complete') AND handling_relation_id IS NOT NULL
        AND relation_from_status='in_progress'
            AND relation_to_status IN ('submitted','awaiting_department_review')
            AND to_status IN
        ('in_progress','awaiting_department_review','awaiting_acceptance','completed')
        AND char_length(text) BETWEEN 1 AND 10000)
          OR (operation='department_review' AND handling_relation_id IS NOT NULL AND
        relation_from_status='awaiting_department_review'
            AND relation_to_status IN ('in_progress','submitted') AND payload ?
        'approved'
            AND (relation_to_status='submitted' OR char_length(btrim(text))>0)
            AND to_status IN
        ('in_progress','awaiting_department_review','awaiting_acceptance','completed'))
          OR (operation='submit_summary' AND handling_relation_id IS NULL AND
        relation_from_status IS NULL AND relation_to_status IS NULL
            AND to_status IN ('awaiting_acceptance','completed') AND char_length(text)
        BETWEEN 1 AND 10000)
          OR (operation='confirm' AND handling_relation_id IS NULL AND
        from_status='awaiting_acceptance' AND to_status='completed' AND text IS NULL)
          OR (operation='return' AND handling_relation_id IS NULL AND
        from_status='awaiting_acceptance' AND to_status='in_progress' AND
        char_length(btrim(text))>0)
          OR (operation='revise' AND handling_relation_id IS NULL AND to_status IN
        ('assigned','department_pending','in_progress') AND payload ? 'before' AND
        payload ? 'after')
          OR (operation='withdraw' AND handling_relation_id IS NULL AND
        to_status='withdrawn' AND char_length(btrim(text))>0)
          OR (operation='extend' AND handling_relation_id IS NULL AND
        from_status=to_status AND payload ? 'after_due_at')
          OR (operation='reassign' AND handling_relation_id IS NULL AND to_status IN
        ('assigned','department_pending') AND payload ? 'replacement_relation_ids')
        ))) IS TRUE"""
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE work_objects,work_object_lifecycle_events,"
            + ",".join(_TABLES)
            + " IN ACCESS EXCLUSIVE MODE"
        )
    )
    has_data = connection.execute(
        sa.text(
            "SELECT "
            + " OR ".join("EXISTS(SELECT 1 FROM " + table + ")" for table in _TABLES)
            + " OR EXISTS(SELECT 1 FROM work_objects WHERE "
            + " OR ".join(c + " IS NOT NULL" for c in _ROOT_COLUMNS)
            + ") OR EXISTS(SELECT 1 FROM work_object_lifecycle_events WHERE "
            + " OR ".join(c + " IS NOT NULL" for c in _EVENT_COLUMNS)
            + ")"
        )
    ).scalar_one()
    if has_data:
        raise RuntimeError("work_dispatch_v2_downgrade_irreversible")
    _event_constraints(upgrade=False)
    op.drop_constraint("fk_work_event_relation", "work_object_lifecycle_events", type_="foreignkey")
    for table in reversed(_TABLES):
        op.drop_table(table)
    for name in ("ix_work_objects_sent_status", "ix_work_objects_withdrawn"):
        op.drop_index(name, table_name="work_objects")
    for name in (
        "ck_work_objects_dispatch_v2_modes",
        "ck_work_objects_lifecycle_fields",
        "ck_work_objects_manual_dispatch_complete",
    ):
        op.drop_constraint(name, "work_objects", type_="check")
    op.create_check_constraint(
        "ck_work_objects_lifecycle_fields", "work_objects", _LEGACY_LIFECYCLE
    )
    op.create_check_constraint(
        "ck_work_objects_manual_dispatch_complete", "work_objects", _LEGACY_MANUAL
    )
    for column in reversed(_EVENT_COLUMNS):
        op.drop_column("work_object_lifecycle_events", column)
    for column in reversed(_ROOT_COLUMNS):
        op.drop_column("work_objects", column)


def _execute_statements(script: str) -> None:
    # asyncpg accepts one DDL statement per execute; this schema has no procedural bodies.
    for statement in script.split(";"):
        if statement.strip():
            op.execute(statement)
