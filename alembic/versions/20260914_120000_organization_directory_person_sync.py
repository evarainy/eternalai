"""Add person names and atomic directory synchronization metadata."""

from alembic import op

revision = "20260914_120000"
down_revision = "20260910_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE organization_user_memberships ADD COLUMN display_name TEXT NULL
    """)
    op.execute("""
        ALTER TABLE organization_user_memberships
          ADD CONSTRAINT ck_org_membership_display_name CHECK (
            display_name IS NULL OR (
              char_length(display_name) BETWEEN 1 AND 200
              AND display_name !~ '^[[:space:]]|[[:space:]]$'
              AND display_name !~ '[[:cntrl:]<>]'
            )
          )
    """)
    op.execute("""
        CREATE TABLE organization_directory_sync_state (
          singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
          snapshot_version BIGINT NOT NULL DEFAULT 0 CHECK (snapshot_version >= 0),
          source_fetched_at TIMESTAMPTZ NULL,
          last_success_at TIMESTAMPTZ NULL,
          last_attempt_started_at TIMESTAMPTZ NULL,
          last_attempt_finished_at TIMESTAMPTZ NULL,
          last_attempt_status TEXT NOT NULL DEFAULT 'never',
          last_error_code TEXT NULL,
          CONSTRAINT ck_org_sync_success CHECK (
            (snapshot_version = 0 AND source_fetched_at IS NULL AND last_success_at IS NULL)
            OR (snapshot_version > 0 AND source_fetched_at IS NOT NULL
                AND last_success_at IS NOT NULL AND source_fetched_at <= last_success_at)
          ),
          CONSTRAINT ck_org_sync_attempt CHECK (
            (last_attempt_status = 'never' AND snapshot_version = 0 AND last_attempt_started_at IS NULL
              AND last_attempt_finished_at IS NULL AND last_error_code IS NULL)
            OR (last_attempt_status = 'running' AND last_attempt_started_at IS NOT NULL
              AND last_attempt_finished_at IS NULL AND last_error_code IS NULL)
            OR (last_attempt_status = 'succeeded' AND last_attempt_started_at IS NOT NULL
              AND last_attempt_finished_at IS NOT NULL
              AND last_attempt_finished_at >= last_attempt_started_at
              AND last_error_code IS NULL)
            OR (last_attempt_status = 'failed' AND last_attempt_started_at IS NOT NULL
              AND last_attempt_finished_at IS NOT NULL
              AND last_attempt_finished_at >= last_attempt_started_at
              AND last_error_code IS NOT NULL)
          ),
          CONSTRAINT ck_org_sync_error CHECK (
            last_error_code IS NULL OR last_error_code IN (
              'source_unconfigured', 'source_binding_unavailable',
              'source_authentication_failed', 'source_timeout', 'source_unavailable',
              'snapshot_incomplete',
              'snapshot_invalid', 'storage_unavailable', 'sync_interrupted'
            )
          ),
          CONSTRAINT ck_org_sync_success_attempt CHECK (
            last_attempt_status <> 'succeeded' OR (
              snapshot_version > 0 AND last_success_at IS NOT NULL
              AND last_success_at = last_attempt_finished_at
            )
          )
        )
    """)
    op.execute("""
        INSERT INTO organization_directory_sync_state(singleton_id) VALUES (1)
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE organization_directory_sync_state
    """)
    op.execute("""
        ALTER TABLE organization_user_memberships DROP CONSTRAINT ck_org_membership_display_name
    """)
    op.execute("""
        ALTER TABLE organization_user_memberships DROP COLUMN display_name
    """)
