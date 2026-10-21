"""Persistent browser binding leases and explicit provider capacity limits."""

import sqlalchemy as sa

from alembic import op

revision = "20261002_211000"
down_revision = "20261002_210000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE browser_capacity_limits (
            quota_id TEXT PRIMARY KEY,
            provider_key TEXT NOT NULL,
            tenant_id TEXT,
            max_active INTEGER NOT NULL CHECK (max_active > 0),
            revision BIGINT NOT NULL DEFAULT 0
                CHECK (revision BETWEEN 0 AND 9007199254740991),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                CHECK (isfinite(updated_at)),
            CONSTRAINT ck_browser_quota_id CHECK (quota_id ~ '^[A-Za-z0-9_-]{1,96}$'),
            CONSTRAINT ck_browser_quota_provider
                CHECK (provider_key ~ '^[A-Za-z0-9_-]{1,96}$'),
            CONSTRAINT ck_browser_quota_tenant
                CHECK (tenant_id IS NULL OR tenant_id ~ '^[A-Za-z0-9_-]{1,96}$')
        )
    """))
    op.execute(sa.text("""CREATE UNIQUE INDEX uq_browser_capacity_global
        ON browser_capacity_limits(provider_key) WHERE tenant_id IS NULL"""))
    op.execute(sa.text("""CREATE UNIQUE INDEX uq_browser_capacity_tenant
        ON browser_capacity_limits(provider_key,tenant_id) WHERE tenant_id IS NOT NULL"""))
    op.execute(sa.text("""
        CREATE TABLE browser_binding_leases (
            tenant_id TEXT NOT NULL,
            ai_user_id TEXT NOT NULL,
            target_system TEXT NOT NULL,
            binding_id TEXT NOT NULL,
            lease_epoch BIGINT NOT NULL DEFAULT 0,
            lease_revision BIGINT NOT NULL DEFAULT 0,
            holder_id TEXT,
            holder_session_id TEXT,
            binding_revision BIGINT,
            authorization_revision BIGINT,
            auth_session_fingerprint BYTEA,
            auth_expires_at TIMESTAMPTZ,
            deadline TIMESTAMPTZ,
            state TEXT NOT NULL DEFAULT 'released',
            acquisition_phase TEXT NOT NULL DEFAULT 'reservation_only',
            acquisition_send_started BOOLEAN NOT NULL DEFAULT FALSE,
            acquisition_operation_id TEXT,
            provider_key TEXT,
            capacity_held BOOLEAN NOT NULL DEFAULT FALSE,
            resource_cipher_version TEXT,
            resource_key_id TEXT,
            resource_nonce BYTEA,
            encrypted_resource_ref BYTEA,
            resource_proof_digest BYTEA,
            release_outcome TEXT,
            release_proof_digest BYTEA,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tenant_id,ai_user_id,target_system,binding_id),
            CONSTRAINT uq_browser_lease_epoch UNIQUE
                (tenant_id,ai_user_id,target_system,binding_id,lease_epoch),
            CONSTRAINT fk_browser_lease_binding FOREIGN KEY
                (tenant_id,ai_user_id,target_system,binding_id)
                REFERENCES oa_session_credentials
                    (tenant_id,ai_user_id,target_system,binding_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT,
            CONSTRAINT fk_browser_lease_session FOREIGN KEY (tenant_id,holder_session_id)
                REFERENCES sessions(tenant_id,session_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT,
            CONSTRAINT ck_browser_lease_revisions CHECK (
                lease_epoch BETWEEN 0 AND 9007199254740991
                AND lease_revision BETWEEN 0 AND 9007199254740991
                AND (binding_revision IS NULL OR binding_revision BETWEEN 1 AND 9007199254740991)
                AND (authorization_revision IS NULL OR
                     authorization_revision BETWEEN 0 AND 9007199254740991)),
            CONSTRAINT ck_browser_lease_identity CHECK (
                tenant_id ~ '^[A-Za-z0-9_-]{1,96}$'
                AND ai_user_id ~ '^[A-Za-z0-9_-]{1,96}$'
                AND target_system IN ('oa','u8','hikvision_ivms')
                AND binding_id ~ '^[A-Za-z0-9_-]{1,96}$'
                AND (holder_id IS NULL OR holder_id ~ '^[A-Za-z0-9_-]{1,96}$')
                AND (holder_session_id IS NULL OR
                     holder_session_id ~ '^[A-Za-z0-9_.-]{1,192}$')
                AND (acquisition_operation_id IS NULL OR
                     acquisition_operation_id ~ '^[A-Za-z0-9_-]{1,96}$')
                AND (provider_key IS NULL OR provider_key ~ '^[A-Za-z0-9_-]{1,96}$')),
            CONSTRAINT ck_browser_lease_dates CHECK (
                isfinite(updated_at) AND (deadline IS NULL OR isfinite(deadline))
                AND (auth_expires_at IS NULL OR isfinite(auth_expires_at))),
            CONSTRAINT ck_browser_lease_digests CHECK (
                (auth_session_fingerprint IS NULL OR octet_length(auth_session_fingerprint)=32)
                AND (resource_proof_digest IS NULL OR octet_length(resource_proof_digest)=32)
                AND (release_proof_digest IS NULL OR octet_length(release_proof_digest)=32)),
            CONSTRAINT ck_browser_lease_envelope CHECK (
                (resource_cipher_version IS NULL AND resource_key_id IS NULL
                 AND resource_nonce IS NULL AND encrypted_resource_ref IS NULL)
                OR (resource_cipher_version IS NOT NULL
                    AND resource_cipher_version='aes256gcm-browser-resource-v1'
                    AND resource_key_id IS NOT NULL AND length(resource_key_id)>0
                    AND resource_nonce IS NOT NULL AND octet_length(resource_nonce)=12
                    AND encrypted_resource_ref IS NOT NULL
                    AND octet_length(encrypted_resource_ref)>=16)),
            CONSTRAINT ck_browser_lease_state CHECK (
                state IN ('released','held','quarantined')),
            CONSTRAINT ck_browser_lease_claim CHECK (
                (state IN ('held','quarantined') AND capacity_held
                 AND lease_epoch>0 AND lease_revision>0
                 AND holder_id IS NOT NULL AND holder_session_id IS NOT NULL
                 AND binding_revision IS NOT NULL AND authorization_revision IS NOT NULL
                 AND auth_session_fingerprint IS NOT NULL AND auth_expires_at IS NOT NULL
                 AND deadline IS NOT NULL AND provider_key IS NOT NULL
                 AND acquisition_operation_id IS NOT NULL)
                OR (state='released' AND NOT capacity_held
                    AND holder_id IS NULL AND holder_session_id IS NULL
                    AND binding_revision IS NULL AND authorization_revision IS NULL
                    AND auth_session_fingerprint IS NULL AND auth_expires_at IS NULL
                    AND deadline IS NULL AND provider_key IS NULL
                    AND acquisition_operation_id IS NULL AND resource_cipher_version IS NULL
                    AND acquisition_phase='reservation_only' AND NOT acquisition_send_started
                    AND resource_proof_digest IS NULL
                    AND (release_outcome IS NULL OR release_outcome IN ('released','terminated')))),
            CONSTRAINT ck_browser_lease_acquisition CHECK (
                (acquisition_phase='reservation_only' AND NOT acquisition_send_started
                 AND resource_cipher_version IS NULL)
                OR (acquisition_phase IN ('acquiring','unknown') AND acquisition_send_started)
                OR (acquisition_phase='acquired' AND acquisition_send_started
                    AND resource_cipher_version IS NOT NULL
                    AND resource_proof_digest IS NOT NULL)),
            CONSTRAINT ck_browser_lease_release CHECK (
                (release_outcome IS NULL AND release_proof_digest IS NULL)
                OR (release_outcome IS NOT NULL
                    AND release_outcome IN ('released','terminated','quarantined','unknown')
                    AND release_proof_digest IS NOT NULL))
        )
    """))
    op.execute(sa.text("""CREATE INDEX ix_browser_lease_reconcile
        ON browser_binding_leases(deadline,updated_at) WHERE state IN ('held','quarantined')"""))
    op.execute(sa.text("""CREATE INDEX ix_browser_lease_capacity
        ON browser_binding_leases(provider_key,tenant_id) WHERE capacity_held"""))
    op.execute(sa.text("""CREATE INDEX ix_browser_lease_holder
        ON browser_binding_leases(tenant_id,ai_user_id,holder_session_id,holder_id)
        WHERE state='held'"""))


def downgrade() -> None:
    connection = op.get_bind()
    if not connection.execute(sa.text(
        "SELECT pg_try_advisory_xact_lock(746420211000)"
    )).scalar_one():
        raise RuntimeError("browser_lease_downgrade_writer_active")
    connection.execute(sa.text(
        "LOCK TABLE browser_capacity_limits, browser_binding_leases IN ACCESS EXCLUSIVE MODE"
    ))
    if connection.execute(sa.text("""SELECT
        EXISTS(SELECT 1 FROM browser_binding_leases)
        OR EXISTS(SELECT 1 FROM browser_capacity_limits)""")).scalar_one():
        raise RuntimeError("browser_lease_downgrade_requires_empty_tables")
    op.drop_table("browser_binding_leases")
    op.drop_table("browser_capacity_limits")
