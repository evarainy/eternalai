"""Atomic OA pending reconciliation; no legacy backfill."""
from alembic import op
from sqlalchemy import text

revision = "20260915_120000"
down_revision = "20260914_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE oa_work_sync_state (
    tenant_id text NOT NULL,
    ai_user_id text NOT NULL,
    stream text NOT NULL,
    issued_generation bigint NOT NULL DEFAULT 0,
    applied_generation bigint NOT NULL DEFAULT 0,
    last_attempt_status text NOT NULL DEFAULT 'never',
    last_attempt_started_at timestamptz,
    last_attempt_finished_at timestamptz,
    last_success_at timestamptz,
    last_error_code text,
    PRIMARY KEY (tenant_id, ai_user_id, stream),
    CONSTRAINT ck_owss_scope CHECK
      (tenant_id = 'default' AND length(btrim(ai_user_id)) > 0),
    CONSTRAINT ck_owss_stream CHECK (stream IN ('pending', 'completed')),
    CONSTRAINT ck_owss_generations CHECK
      (issued_generation >= 0 AND applied_generation >= 0
       AND applied_generation <= issued_generation),
    CONSTRAINT ck_owss_status CHECK
      (last_attempt_status IN ('never', 'running', 'succeeded', 'failed')),
    CONSTRAINT ck_owss_success CHECK
      (((applied_generation = 0 AND last_success_at IS NULL)
        OR (applied_generation > 0 AND last_success_at IS NOT NULL)) IS TRUE),
    CONSTRAINT ck_owss_attempt CHECK ((
      (last_attempt_status = 'never' AND issued_generation = 0 AND applied_generation = 0
       AND last_attempt_started_at IS NULL AND last_attempt_finished_at IS NULL AND last_error_code IS NULL)
      OR (last_attempt_status = 'running' AND issued_generation > applied_generation
          AND last_attempt_started_at IS NOT NULL AND last_attempt_finished_at IS NULL AND last_error_code IS NULL)
      OR (last_attempt_status = 'succeeded' AND issued_generation = applied_generation
          AND applied_generation > 0 AND last_attempt_started_at IS NOT NULL
          AND last_attempt_finished_at IS NOT NULL AND last_success_at = last_attempt_finished_at
          AND last_attempt_finished_at >= last_attempt_started_at AND last_error_code IS NULL)
      OR (last_attempt_status = 'failed' AND issued_generation > applied_generation
          AND last_attempt_started_at IS NOT NULL AND last_attempt_finished_at IS NOT NULL
          AND (last_attempt_finished_at >= last_attempt_started_at OR last_error_code = 'clock_invalid')
          AND last_error_code IS NOT NULL)
    ) IS TRUE),
    CONSTRAINT ck_owss_failure CHECK
      (last_error_code IS NULL OR last_error_code IN
       ('reauthentication_required', 'binding_scope_required', 'forbidden',
        'invalid_response', 'upstream_unavailable', 'storage_unavailable', 'clock_invalid'))
)""")
    op.execute("""CREATE TABLE oa_work_pending_observations (
    tenant_id text NOT NULL,
    ai_user_id text NOT NULL,
    source_ref text NOT NULL,
    work_object_id text NOT NULL,
    pending_state text NOT NULL,
    revision bigint NOT NULL,
    last_seen_at timestamptz NOT NULL,
    last_checked_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, ai_user_id, source_ref),
    CONSTRAINT uq_owpo_work_object UNIQUE (work_object_id),
    CONSTRAINT fk_owpo_work_object FOREIGN KEY (work_object_id)
      REFERENCES work_objects(work_object_id) ON DELETE RESTRICT,
    CONSTRAINT ck_owpo_scope CHECK
      (tenant_id = 'default' AND length(btrim(ai_user_id)) > 0
       AND length(btrim(source_ref)) > 0),
    CONSTRAINT ck_owpo_state CHECK (pending_state IN ('current', 'unconfirmed')),
    CONSTRAINT ck_owpo_revision CHECK (revision > 0),
    CONSTRAINT ck_owpo_times CHECK
      (last_checked_at >= last_seen_at
       AND (pending_state <> 'current' OR last_checked_at = last_seen_at))
)""")
    op.execute("""CREATE INDEX ix_owpo_subject_state
    ON oa_work_pending_observations (tenant_id, ai_user_id, pending_state)""")


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(text("LOCK TABLE oa_work_sync_state IN ACCESS EXCLUSIVE MODE"))
    connection.execute(text("LOCK TABLE oa_work_pending_observations IN ACCESS EXCLUSIVE MODE"))
    completion_exists = connection.execute(
        text("SELECT to_regclass('oa_work_completion_facts') IS NOT NULL")
    ).scalar_one()
    populated = connection.execute(text(
        "SELECT EXISTS (SELECT 1 FROM oa_work_sync_state) "
        "OR EXISTS (SELECT 1 FROM oa_work_pending_observations)"
    )).scalar_one()
    if completion_exists or populated:
        raise RuntimeError("OA reconciliation data exists; downgrade refused.")
    op.execute("DROP INDEX ix_owpo_subject_state")
    op.execute("DROP TABLE oa_work_pending_observations")
    op.execute("DROP TABLE oa_work_sync_state")
