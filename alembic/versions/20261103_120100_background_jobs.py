"""User-visible background job ledger (FOUNDATION_PLAN v1.6 appendix B.1).

Upgrade-only authorization: P2-BACKGROUND-FOUNDATION-001 (DECISIONS 2026-11-03).
Like the other revisions the table is created unqualified, so it lands in
``public`` on the authorized targets and inside an isolated schema when the chain is
replayed there. Downgrade only drops an empty table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261103_120100"
down_revision = "20261103_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE background_jobs (
        job_id UUID PRIMARY KEY,
        tenant_id TEXT NOT NULL CHECK (char_length(tenant_id) BETWEEN 1 AND 256),
        requested_by_ai_user_id TEXT NOT NULL
          CHECK (char_length(requested_by_ai_user_id) BETWEEN 1 AND 256),
        session_id TEXT CHECK (session_id IS NULL OR char_length(session_id) BETWEEN 1 AND 256),
        job_kind TEXT NOT NULL CHECK (job_kind ~ '^[a-z][a-z0-9_.]{2,63}$'),
        external_effect TEXT NOT NULL CHECK (external_effect IN ('none','read_only')),
        params JSONB NOT NULL DEFAULT '{}'::jsonb
          CHECK (jsonb_typeof(params) = 'object' AND pg_column_size(params) <= 8192),
        idempotency_key UUID NOT NULL,
        request_fingerprint BYTEA NOT NULL CHECK (octet_length(request_fingerprint) = 32),
        status TEXT NOT NULL CHECK (status IN
          ('accepted','running','cancel_requested','succeeded','failed','cancelled')),
        queue_job_id BIGINT NOT NULL UNIQUE,
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 10),
        claim_token UUID,
        heartbeat_at TIMESTAMPTZ,
        dispatch_count INTEGER NOT NULL DEFAULT 1 CHECK (dispatch_count BETWEEN 1 AND 3),
        result_code TEXT CHECK (result_code IS NULL OR result_code ~ '^[a-z][a-z0-9_]{0,63}$'),
        result_summary JSONB CHECK (result_summary IS NULL OR
          (jsonb_typeof(result_summary) = 'object' AND pg_column_size(result_summary) <= 4096)),
        created_at TIMESTAMPTZ NOT NULL,
        started_at TIMESTAMPTZ,
        cancel_requested_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL,
        CHECK ((status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)),
        CHECK (status <> 'cancel_requested' OR cancel_requested_at IS NOT NULL)
      )
    """)
    op.execute(
        "CREATE UNIQUE INDEX ux_background_jobs_idempotency "
        "ON background_jobs (tenant_id, requested_by_ai_user_id, idempotency_key)"
    )
    op.execute(
        "CREATE INDEX ix_background_jobs_owner_recent "
        "ON background_jobs (tenant_id, requested_by_ai_user_id, created_at DESC, job_id)"
    )
    op.execute(
        "CREATE INDEX ix_background_jobs_owner_active "
        "ON background_jobs (tenant_id, requested_by_ai_user_id) "
        "WHERE status IN ('accepted','running','cancel_requested')"
    )
    op.execute(
        "CREATE INDEX ix_background_jobs_nonterminal ON background_jobs (updated_at) "
        "WHERE status IN ('accepted','running','cancel_requested')"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE background_jobs IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT EXISTS(SELECT 1 FROM background_jobs)")).scalar_one():
        raise RuntimeError("background_jobs_downgrade_irreversible")
    op.drop_table("background_jobs")
