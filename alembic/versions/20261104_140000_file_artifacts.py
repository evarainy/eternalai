"""File artifact metadata (FOUNDATION_PLAN v1.6 appendix B.2).

Upgrade-only authorization: P2-FILE-ARTIFACTS-001 (DECISIONS 2026-11-03). It follows the IM
migrations (``20261104_130000``) so the chain keeps a single head. Like the other
revisions the table and its foreign keys are unqualified, so they land in ``public`` on
the authorized targets and inside an isolated schema when the chain is replayed there.
Downgrade only drops an empty table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261104_140000"
down_revision = "20261104_130000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE file_artifacts (
        artifact_id UUID PRIMARY KEY,
        tenant_id TEXT NOT NULL CHECK (char_length(tenant_id) BETWEEN 1 AND 256),
        owner_ai_user_id TEXT NOT NULL CHECK (char_length(owner_ai_user_id) BETWEEN 1 AND 256),
        session_id TEXT CHECK (session_id IS NULL OR char_length(session_id) BETWEEN 1 AND 256),
        purpose TEXT NOT NULL CHECK (purpose IN ('chat_input','job_output')),
        source_job_id UUID REFERENCES background_jobs(job_id),
        source_artifact_id UUID REFERENCES file_artifacts(artifact_id),
        display_filename TEXT NOT NULL CHECK (char_length(display_filename) BETWEEN 1 AND 255),
        media_type TEXT NOT NULL CHECK (char_length(media_type) BETWEEN 3 AND 128),
        size_bytes BIGINT CHECK (size_bytes IS NULL OR size_bytes > 0),
        sha256 BYTEA CHECK (sha256 IS NULL OR octet_length(sha256) = 32),
        inspection JSONB CHECK (inspection IS NULL OR
          (jsonb_typeof(inspection) = 'object' AND pg_column_size(inspection) <= 8192)),
        storage_scope TEXT NOT NULL CHECK (storage_scope ~ '^[0-9a-f]{64}$'),
        storage_reference TEXT NOT NULL CHECK (storage_reference ~ '^[A-Za-z0-9_-]{43}$'),
        state TEXT NOT NULL CHECK (state IN ('pending','available','expired','removed')),
        sealed_at TIMESTAMPTZ,
        lifecycle_started_at TIMESTAMPTZ,
        expires_at TIMESTAMPTZ,
        lease_expires_at TIMESTAMPTZ,
        upload_idempotency_key UUID,
        request_fingerprint BYTEA
          CHECK (request_fingerprint IS NULL OR octet_length(request_fingerprint) = 32),
        mac_key_id TEXT NOT NULL CHECK (mac_key_id ~ '^[A-Za-z0-9_-]{1,32}$'),
        mac TEXT NOT NULL CHECK (mac ~ '^[0-9a-f]{64}$'),
        removal_attempts INTEGER NOT NULL DEFAULT 0 CHECK (removal_attempts >= 0),
        last_removal_error TEXT
          CHECK (last_removal_error IS NULL OR last_removal_error ~ '^[a-z][a-z0-9_]{0,63}$'),
        created_at TIMESTAMPTZ NOT NULL,
        expired_at TIMESTAMPTZ,
        removed_at TIMESTAMPTZ,
        CONSTRAINT ux_file_artifacts_storage UNIQUE (storage_scope, storage_reference),
        CHECK (state <> 'pending' OR sealed_at IS NULL),
        CHECK (state NOT IN ('available','expired') OR sealed_at IS NOT NULL),
        CHECK (sealed_at IS NULL OR (size_bytes IS NOT NULL AND sha256 IS NOT NULL
               AND lifecycle_started_at IS NOT NULL AND expires_at IS NOT NULL)),
        CHECK ((lifecycle_started_at IS NULL) = (expires_at IS NULL)),
        CHECK (expires_at IS NULL OR (expires_at > lifecycle_started_at
               AND expires_at <= lifecycle_started_at + INTERVAL '14 days')),
        CHECK ((purpose = 'chat_input') = (upload_idempotency_key IS NOT NULL)),
        CHECK ((purpose = 'chat_input') = (request_fingerprint IS NOT NULL)),
        CHECK (purpose <> 'chat_input' OR session_id IS NOT NULL),
        CHECK (purpose <> 'chat_input' OR sealed_at IS NULL OR inspection IS NOT NULL),
        CHECK (purpose <> 'job_output' OR source_job_id IS NOT NULL),
        CHECK ((state = 'removed') = (removed_at IS NOT NULL)),
        CHECK (state <> 'expired' OR expired_at IS NOT NULL),
        CHECK ((state = 'pending') = (lease_expires_at IS NOT NULL))
      )
    """)
    op.execute(
        "CREATE UNIQUE INDEX ux_file_artifacts_upload_idempotency "
        "ON file_artifacts (tenant_id, owner_ai_user_id, upload_idempotency_key) "
        "WHERE upload_idempotency_key IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_file_artifacts_owner_quota "
        "ON file_artifacts (tenant_id, owner_ai_user_id, session_id) "
        "WHERE state IN ('pending','available')"
    )
    op.execute(
        "CREATE INDEX ix_file_artifacts_due "
        "ON file_artifacts (storage_scope, expires_at) WHERE state IN ('available','expired')"
    )
    op.execute(
        "CREATE INDEX ix_file_artifacts_pending "
        "ON file_artifacts (storage_scope, lease_expires_at) WHERE state = 'pending'"
    )
    op.execute(
        "CREATE INDEX ix_file_artifacts_source_job "
        "ON file_artifacts (source_job_id) WHERE source_job_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_file_artifacts_source_artifact "
        "ON file_artifacts (source_artifact_id) WHERE source_artifact_id IS NOT NULL"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE file_artifacts IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT EXISTS(SELECT 1 FROM file_artifacts)")).scalar_one():
        raise RuntimeError("file_artifacts_downgrade_irreversible")
    op.drop_table("file_artifacts")
