"""Durable READ_ONLY browser runs, publications and immutable profile history.

Only empty-history rollback is supported; downgrade never deletes business rows.
Application writers must hold shared advisory lock 746420212000. Lease writers
also retain 746420211000; downgrade acquires both locks exclusively.
"""

import sqlalchemy as sa

from alembic import op

revision = "20261003_212000"
down_revision = "20261002_211000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        ALTER TABLE tasks
        ADD COLUMN request_schema_version TEXT,
        ADD COLUMN client_request_id TEXT,
        ADD COLUMN request_digest BYTEA,
        ADD COLUMN request_digest_key_id TEXT,
        ADD COLUMN processing_owner TEXT,
        ADD COLUMN processing_deadline TIMESTAMPTZ,
        ADD CONSTRAINT ck_browser_task_request CHECK (
        (request_schema_version IS NULL AND client_request_id IS NULL AND request_digest IS NULL
        AND request_digest_key_id IS NULL)
        OR (request_schema_version IS NOT NULL AND request_schema_version='browser.request.v1' AND
        client_request_id IS NOT NULL
        AND client_request_id ~ '^[A-Za-z0-9_-]{1,96}$'
        AND request_digest IS NOT NULL AND octet_length(request_digest)=32
        AND request_digest_key_id IS NOT NULL AND length(btrim(request_digest_key_id))>0
        AND tenant_id IS NOT NULL AND length(btrim(tenant_id))>0)),
        ADD CONSTRAINT ck_browser_task_processing CHECK (
        (processing_owner IS NULL AND processing_deadline IS NULL)
        OR (request_digest IS NOT NULL AND processing_owner IS NOT NULL
        AND processing_owner ~ '^[A-Za-z0-9_-]{1,96}$'
        AND processing_deadline IS NOT NULL AND isfinite(processing_deadline))),
        ADD CONSTRAINT uq_browser_task_owner UNIQUE(tenant_id, ai_user_id, session_id, task_id)
    """)
    )
    op.execute(
        sa.text("""
        CREATE UNIQUE INDEX uq_browser_task_request
        ON tasks(tenant_id, ai_user_id, session_id, client_request_id)
        WHERE client_request_id IS NOT NULL
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE task_events ADD CONSTRAINT uq_browser_task_event_parent UNIQUE(task_id,
        event_id)
    """)
    )
    op.execute(
        sa.text("""
        CREATE TABLE browser_publications (
        tenant_id TEXT NOT NULL CHECK(length(btrim(tenant_id))>0),
        publication_digest BYTEA NOT NULL CHECK(octet_length(publication_digest)=32),
        skill_id TEXT NOT NULL CHECK(skill_id ~ '^[A-Za-z0-9_-]{1,96}$'),
        skill_version TEXT NOT NULL CHECK(skill_version ~ '^[A-Za-z0-9_-]{1,96}$'),
        capability_id TEXT NOT NULL CHECK(length(btrim(capability_id))>0),
        capability_digest BYTEA NOT NULL CHECK(octet_length(capability_digest)=32),
        manifest JSONB NOT NULL CHECK(jsonb_typeof(manifest)='object'),
        state TEXT NOT NULL DEFAULT 'prepared' CHECK(state IN ('prepared', 'active', 'inactive')),
        activation_revision BIGINT NOT NULL DEFAULT 0 CHECK(activation_revision BETWEEN 0 AND
        9007199254740991),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK(isfinite(created_at)),
        activated_at TIMESTAMPTZ CHECK(activated_at IS NULL OR isfinite(activated_at)),
        PRIMARY KEY(tenant_id, publication_digest),
        UNIQUE(tenant_id, skill_id, skill_version),
        CHECK((state='prepared' AND activation_revision=0 AND activated_at IS NULL)
        OR (state IN ('active', 'inactive') AND activation_revision>0 AND activated_at IS NOT
        NULL))
        )
    """)
    )
    op.execute(
        sa.text("""
        CREATE UNIQUE INDEX uq_browser_publication_active
        ON browser_publications(tenant_id, skill_id) WHERE state='active'
    """)
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION browser_publication_content_immutable() RETURNS trigger LANGUAGE plpgsql
        AS $$
        BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'browser_publication_delete_forbidden'; END IF;
        IF (to_jsonb(NEW)-ARRAY['state', 'activation_revision', 'activated_at'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state', 'activation_revision', 'activated_at'])
        THEN RAISE EXCEPTION 'browser_publication_content_immutable'; END IF;
        IF NOT ((OLD.state='prepared' AND NEW.state='active')
        OR (OLD.state='active' AND NEW.state='inactive'))
        THEN RAISE EXCEPTION 'browser_publication_transition_invalid'; END IF;
        IF OLD.activated_at IS NOT NULL AND NEW.activated_at IS DISTINCT FROM OLD.activated_at
        THEN RAISE EXCEPTION 'browser_publication_activation_time_immutable'; END IF;
        IF NEW.activation_revision<>OLD.activation_revision+1
        THEN RAISE EXCEPTION 'browser_publication_revision_invalid'; END IF;
        RETURN NEW;
        END $$
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER browser_publication_immutable BEFORE UPDATE OR DELETE ON
        browser_publications
        FOR EACH ROW EXECUTE FUNCTION browser_publication_content_immutable()
    """)
    )
    op.execute(
        sa.text("""
        CREATE TABLE browser_profiles (
        tenant_id TEXT NOT NULL,
        ai_user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        target_system TEXT NOT NULL CHECK(target_system IN ('oa', 'u8', 'hikvision_ivms')),
        binding_id TEXT NOT NULL,
        profile_revision BIGINT NOT NULL DEFAULT 0 CHECK(profile_revision BETWEEN 0 AND
        9007199254740991),
        active_generation_id TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK(isfinite(updated_at)),
        PRIMARY KEY(tenant_id, ai_user_id, session_id, target_system, binding_id),
        FOREIGN KEY(tenant_id, session_id) REFERENCES sessions(tenant_id, session_id) ON DELETE
        RESTRICT,
        FOREIGN KEY(tenant_id, ai_user_id, target_system, binding_id)
        REFERENCES oa_session_credentials(tenant_id, ai_user_id, target_system, binding_id) ON
        DELETE RESTRICT,
        CHECK(active_generation_id IS NULL OR profile_revision>0)
        )
    """)
    )
    op.execute(
        sa.text("""
        CREATE TABLE browser_profile_generations (
        generation_id TEXT PRIMARY KEY CHECK(generation_id ~ '^[A-Za-z0-9_-]{1,96}$'),
        tenant_id TEXT NOT NULL,
        ai_user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        target_system TEXT NOT NULL,
        binding_id TEXT NOT NULL,
        binding_revision BIGINT NOT NULL CHECK(binding_revision BETWEEN 1 AND 9007199254740991),
        profile_revision BIGINT NOT NULL CHECK(profile_revision BETWEEN 1 AND 9007199254740991),
        lease_epoch BIGINT NOT NULL CHECK(lease_epoch BETWEEN 1 AND 9007199254740991),
        provider_key TEXT NOT NULL CHECK(provider_key ~ '^[A-Za-z0-9_-]{1,96}$'),
        capture_operation_id TEXT NOT NULL CHECK(capture_operation_id ~ '^[A-Za-z0-9_-]{1,96}$'),
        manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
        generation_ref_digest BYTEA NOT NULL CHECK(octet_length(generation_ref_digest)=32),
        subject_digest BYTEA NOT NULL CHECK(octet_length(subject_digest)=32),
        origin_digest BYTEA NOT NULL CHECK(octet_length(origin_digest)=32),
        projection_digest BYTEA NOT NULL CHECK(octet_length(projection_digest)=32),
        captured_bytes BIGINT NOT NULL CHECK(captured_bytes BETWEEN 0 AND 9007199254740991),
        cipher_version TEXT NOT NULL CHECK(cipher_version='aes256gcm-browser-profile-v1'),
        key_id TEXT NOT NULL CHECK(length(btrim(key_id))>0),
        nonce BYTEA NOT NULL CHECK(octet_length(nonce)=12),
        ciphertext BYTEA NOT NULL CHECK(octet_length(ciphertext)>=16),
        disposition TEXT NOT NULL CHECK(disposition IN ('validated', 'promoted', 'retired',
        'orphan', 'cleanup_confirmed')),
        cleanup_proof_digest BYTEA CHECK(cleanup_proof_digest IS NULL OR
        octet_length(cleanup_proof_digest)=32),
        CHECK((disposition='cleanup_confirmed')=(cleanup_proof_digest IS NOT NULL)),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK(isfinite(created_at)),
        UNIQUE(provider_key, generation_ref_digest),
        UNIQUE(provider_key, capture_operation_id),
        UNIQUE(tenant_id, ai_user_id, session_id, target_system, binding_id, generation_id),
        UNIQUE(tenant_id, ai_user_id, session_id, target_system, binding_id, profile_revision,
        generation_id),
        FOREIGN KEY(tenant_id, ai_user_id, session_id, target_system, binding_id)
        REFERENCES browser_profiles(tenant_id, ai_user_id, session_id, target_system, binding_id)
        ON DELETE RESTRICT
        )
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE browser_profiles ADD CONSTRAINT fk_browser_profile_active_generation
        FOREIGN KEY(tenant_id, ai_user_id, session_id, target_system, binding_id,
        profile_revision, active_generation_id)
        REFERENCES browser_profile_generations(tenant_id, ai_user_id, session_id, target_system,
        binding_id, profile_revision, generation_id)
        ON DELETE RESTRICT
    """)
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION browser_profile_generation_immutable() RETURNS trigger LANGUAGE plpgsql AS
        $$
        BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'browser_profile_generation_delete_forbidden'; END
        IF;
        IF (to_jsonb(NEW)-ARRAY['disposition', 'cleanup_proof_digest'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['disposition', 'cleanup_proof_digest'])
        THEN RAISE EXCEPTION 'browser_profile_generation_immutable'; END IF;
        IF NOT ((OLD.disposition='validated' AND NEW.disposition IN ('promoted', 'orphan'))
        OR (OLD.disposition='promoted' AND NEW.disposition='retired')
        OR (OLD.disposition IN ('retired', 'orphan') AND NEW.disposition='cleanup_confirmed'))
        THEN RAISE EXCEPTION 'browser_profile_generation_transition_invalid'; END IF;
        RETURN NEW;
        END $$
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER browser_profile_generation_immutable
        BEFORE UPDATE OR DELETE ON browser_profile_generations
        FOR EACH ROW EXECUTE FUNCTION browser_profile_generation_immutable()
    """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX ix_browser_profile_orphans ON browser_profile_generations(provider_key,
        created_at)
        WHERE disposition IN ('orphan', 'retired')
    """)
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION browser_profile_head_monotonic() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF (NEW.tenant_id, NEW.ai_user_id, NEW.session_id, NEW.target_system, NEW.binding_id)
        IS DISTINCT FROM (OLD.tenant_id, OLD.ai_user_id, OLD.session_id, OLD.target_system,
        OLD.binding_id)
        THEN RAISE EXCEPTION 'browser_profile_owner_immutable'; END IF;
        IF NEW.profile_revision<>OLD.profile_revision+1
        THEN RAISE EXCEPTION 'browser_profile_revision_invalid'; END IF;
        RETURN NEW;
        END $$
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER browser_profile_head_monotonic BEFORE UPDATE ON browser_profiles
        FOR EACH ROW EXECUTE FUNCTION browser_profile_head_monotonic()
    """)
    )
    op.execute(
        sa.text("""
        CREATE TABLE browser_runs (
        run_id TEXT PRIMARY KEY CHECK(run_id ~ '^[A-Za-z0-9_-]{1,96}$'),
        task_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        ai_user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        target_system TEXT NOT NULL CHECK(target_system IN ('oa', 'u8', 'hikvision_ivms')),
        binding_id TEXT NOT NULL,
        binding_revision BIGINT NOT NULL CHECK(binding_revision BETWEEN 1 AND 9007199254740991),
        auth_evidence_version TEXT NOT NULL CHECK(auth_evidence_version='verified-session-v1'),
        auth_fingerprint BYTEA NOT NULL CHECK(octet_length(auth_fingerprint)=32),
        auth_expires_at TIMESTAMPTZ NOT NULL CHECK(isfinite(auth_expires_at)),
        publication_digest BYTEA NOT NULL CHECK(octet_length(publication_digest)=32),
        input_revision BIGINT NOT NULL CHECK(input_revision BETWEEN 1 AND 9007199254740991),
        input_digest BYTEA NOT NULL CHECK(octet_length(input_digest)=32),
        input_cipher_version TEXT NOT NULL
        CHECK(input_cipher_version='aes256gcm-browser-input-v1'),
        input_key_id TEXT NOT NULL CHECK(length(btrim(input_key_id))>0),
        input_nonce BYTEA NOT NULL CHECK(octet_length(input_nonce)=12),
        input_ciphertext BYTEA NOT NULL CHECK(octet_length(input_ciphertext)>=16),
        provider_key TEXT,
        provider_manifest_digest BYTEA,
        lease_epoch BIGINT,
        profile_generation_id TEXT,
        capture_operation_id TEXT UNIQUE CHECK(capture_operation_id IS NULL
        OR capture_operation_id ~ '^[A-Za-z0-9_-]{1,96}$'),
        capture_status TEXT NOT NULL DEFAULT 'not_requested' CHECK(capture_status IN
        ('not_requested', 'prepared', 'sent', 'unknown', 'validated', 'promoted', 'failed',
        'quarantined')),
        CHECK((capture_status='not_requested')=(capture_operation_id IS NULL)),
        worker_id TEXT,
        worker_deadline TIMESTAMPTZ,
        worker_epoch BIGINT NOT NULL DEFAULT 0 CHECK(worker_epoch BETWEEN 0 AND 9007199254740991),
        status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'waiting_user',
        'completed', 'failed', 'cancelled')),
        phase TEXT DEFAULT 'queued' CHECK(phase IS NULL OR phase IN ('queued', 'acquiring',
        'running', 'verifying', 'waiting_user')),
        state_revision BIGINT NOT NULL DEFAULT 0 CHECK(state_revision BETWEEN 0 AND
        9007199254740991),
        cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
        cancel_acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
        effect TEXT NOT NULL DEFAULT 'not_sent' CHECK(effect IN ('not_sent', 'acknowledged',
        'unknown')),
        verification TEXT CHECK(verification IS NULL OR verification IN ('verified', 'mismatch',
        'incomplete', 'unsupported')),
        verification_evidence_digest BYTEA CHECK(verification_evidence_digest IS NULL OR
        octet_length(verification_evidence_digest)=32),
        cleanup TEXT NOT NULL DEFAULT 'pending' CHECK(cleanup IN ('pending', 'released',
        'terminated', 'quarantined', 'failed')),
        error_code TEXT CHECK(error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,95}$'),
        dispatch_failure_code TEXT CHECK(dispatch_failure_code IS NULL OR dispatch_failure_code IN
        (
        'unavailable', 'overloaded', 'invalid_request', 'resource_not_found', 'unsupported',
        'denied', 'stale',
        'subject_mismatch', 'invalid_response', 'timeout', 'cancelled', 'effect_unknown',
        'quarantined')),
        terminal_revision BIGINT,
        terminal_event_id TEXT UNIQUE,
        result_digest BYTEA,
        result_cipher_version TEXT,
        result_key_id TEXT,
        result_nonce BYTEA,
        result_ciphertext BYTEA,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK(isfinite(created_at)),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK(isfinite(updated_at)),
        UNIQUE(tenant_id, ai_user_id, session_id, target_system, binding_id, run_id),
        FOREIGN KEY(tenant_id, ai_user_id, session_id, task_id)
        REFERENCES tasks(tenant_id, ai_user_id, session_id, task_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id, ai_user_id, target_system, binding_id)
        REFERENCES oa_session_credentials(tenant_id, ai_user_id, target_system, binding_id) ON
        DELETE RESTRICT,
        FOREIGN KEY(tenant_id, publication_digest)
        REFERENCES browser_publications(tenant_id, publication_digest) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id, ai_user_id, session_id, target_system, binding_id,
        profile_generation_id)
        REFERENCES browser_profile_generations(tenant_id, ai_user_id, session_id, target_system,
        binding_id, generation_id)
        ON DELETE RESTRICT,
        FOREIGN KEY(task_id, terminal_event_id) REFERENCES task_events(task_id, event_id) ON
        DELETE RESTRICT,
        CHECK((provider_key IS NULL AND provider_manifest_digest IS NULL AND lease_epoch IS NULL)
        OR (provider_key IS NOT NULL AND provider_key ~ '^[A-Za-z0-9_-]{1,96}$'
        AND provider_manifest_digest IS NOT NULL AND octet_length(provider_manifest_digest)=32
        AND lease_epoch IS NOT NULL AND lease_epoch BETWEEN 1 AND 9007199254740991)),
        CHECK(profile_generation_id IS NULL OR lease_epoch IS NOT NULL),
        CHECK((worker_id IS NULL AND worker_deadline IS NULL)
        OR (worker_id IS NOT NULL AND worker_id ~ '^[A-Za-z0-9_-]{1,96}$'
        AND worker_deadline IS NOT NULL AND isfinite(worker_deadline) AND worker_epoch>0)),
        CHECK(NOT cancel_acknowledged OR cancel_requested),
        CHECK(status<>'cancelled' OR (cancel_acknowledged AND error_code IS NOT NULL AND
        error_code='browser_cancelled')),
        CHECK((status IN ('running', 'waiting_user') AND phase IS NOT NULL
        AND terminal_revision IS NULL AND terminal_event_id IS NULL)
        OR (status IN ('completed', 'failed', 'cancelled') AND phase IS NULL
        AND terminal_revision IS NOT NULL AND terminal_revision BETWEEN 1 AND state_revision
        AND terminal_event_id IS NOT NULL)),
        CHECK(status NOT IN ('running', 'waiting_user') OR
        ((status='waiting_user')=(phase='waiting_user'))),
        CHECK(status<>'completed' OR verification IS NOT DISTINCT FROM 'verified'),
        CHECK(verification IS DISTINCT FROM 'verified'
        OR (verification_evidence_digest IS NOT NULL AND result_digest IS NOT NULL)),
        CHECK(status IN ('running', 'waiting_user')
        OR capture_status IN ('not_requested', 'promoted', 'failed', 'quarantined')),
        CHECK(status<>'completed' OR (error_code IS NULL AND result_digest IS NOT NULL AND
        verification_evidence_digest IS NOT NULL)),
        CHECK(status<>'failed' OR error_code IS NOT NULL),
        CHECK(status IN ('running', 'waiting_user') OR effect<>'unknown' OR status='completed' OR
        (status='failed' AND error_code IS NOT NULL AND error_code='browser_effect_unknown')),
        CHECK((result_digest IS NULL AND result_cipher_version IS NULL AND result_key_id IS NULL
        AND result_nonce IS NULL AND result_ciphertext IS NULL)
        OR (result_digest IS NOT NULL AND octet_length(result_digest)=32
        AND result_cipher_version IS NOT NULL AND
        result_cipher_version='aes256gcm-browser-result-v1'
        AND result_key_id IS NOT NULL AND length(btrim(result_key_id))>0
        AND result_nonce IS NOT NULL AND octet_length(result_nonce)=12
        AND result_ciphertext IS NOT NULL AND octet_length(result_ciphertext)>=16))
        )
    """)
    )
    op.execute(
        sa.text("""
        CREATE UNIQUE INDEX uq_browser_run_active_task ON browser_runs(task_id)
        WHERE status IN ('running', 'waiting_user')
    """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX ix_browser_run_queue ON browser_runs(phase, worker_deadline, created_at)
        WHERE status IN ('running', 'waiting_user')
    """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX ix_browser_run_owner ON browser_runs(tenant_id, ai_user_id, session_id,
        task_id)
    """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX ix_browser_run_cleanup ON browser_runs(cleanup, updated_at)
        WHERE cleanup IN ('pending', 'quarantined', 'failed')
    """)
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION browser_run_terminal_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'browser_run_delete_forbidden'; END IF;
        IF (to_jsonb(NEW)-ARRAY['provider_key', 'provider_manifest_digest', 'lease_epoch',
        'profile_generation_id', 'capture_operation_id', 'capture_status', 'worker_id',
        'worker_deadline', 'worker_epoch', 'status', 'phase', 'state_revision',
        'cancel_requested', 'cancel_acknowledged', 'effect', 'verification',
        'verification_evidence_digest', 'cleanup', 'error_code', 'dispatch_failure_code',
        'terminal_revision', 'terminal_event_id', 'result_digest', 'result_cipher_version',
        'result_key_id', 'result_nonce', 'result_ciphertext', 'updated_at'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['provider_key', 'provider_manifest_digest',
        'lease_epoch', 'profile_generation_id', 'capture_operation_id', 'capture_status',
        'worker_id', 'worker_deadline', 'worker_epoch', 'status', 'phase', 'state_revision',
        'cancel_requested', 'cancel_acknowledged', 'effect', 'verification',
        'verification_evidence_digest', 'cleanup', 'error_code', 'dispatch_failure_code',
        'terminal_revision', 'terminal_event_id', 'result_digest', 'result_cipher_version',
        'result_key_id', 'result_nonce', 'result_ciphertext', 'updated_at'])
        THEN RAISE EXCEPTION 'browser_run_authorized_input_immutable'; END IF;
        IF OLD.capture_operation_id IS NOT NULL
        AND NEW.capture_operation_id IS DISTINCT FROM OLD.capture_operation_id
        THEN RAISE EXCEPTION 'browser_capture_operation_immutable'; END IF;
        IF NEW.capture_status IS DISTINCT FROM OLD.capture_status AND NOT (
        (OLD.capture_status='not_requested' AND NEW.capture_status='prepared')
        OR (OLD.capture_status='prepared' AND NEW.capture_status IN ('sent', 'failed',
        'quarantined'))
        OR (OLD.capture_status='sent' AND NEW.capture_status IN ('unknown', 'validated', 'failed',
        'quarantined'))
        OR (OLD.capture_status='unknown' AND NEW.capture_status IN ('validated', 'failed',
        'quarantined'))
        OR (OLD.capture_status='validated' AND NEW.capture_status IN ('promoted', 'failed',
        'quarantined')))
        THEN RAISE EXCEPTION 'browser_capture_transition_invalid'; END IF;
        IF OLD.verification='verified' AND
        (NEW.verification, NEW.verification_evidence_digest, NEW.result_digest,
        NEW.result_cipher_version,
        NEW.result_key_id, NEW.result_nonce, NEW.result_ciphertext)
        IS DISTINCT FROM
        (OLD.verification, OLD.verification_evidence_digest, OLD.result_digest,
        OLD.result_cipher_version,
        OLD.result_key_id, OLD.result_nonce, OLD.result_ciphertext)
        THEN RAISE EXCEPTION 'browser_verified_result_immutable'; END IF;
        IF OLD.status IN ('completed', 'failed', 'cancelled') AND
        (to_jsonb(NEW)-ARRAY['cleanup', 'state_revision', 'updated_at', 'worker_id',
        'worker_deadline', 'worker_epoch'])
        IS DISTINCT FROM
        (to_jsonb(OLD)-ARRAY['cleanup', 'state_revision', 'updated_at', 'worker_id',
        'worker_deadline', 'worker_epoch'])
        THEN RAISE EXCEPTION 'browser_run_terminal_immutable'; END IF;
        IF NEW.state_revision<>OLD.state_revision+1
        THEN RAISE EXCEPTION 'browser_run_revision_invalid'; END IF;
        IF OLD.cancel_requested AND NOT NEW.cancel_requested
        OR OLD.cancel_acknowledged AND NOT NEW.cancel_acknowledged
        THEN RAISE EXCEPTION 'browser_run_cancel_nonmonotonic'; END IF;
        RETURN NEW;
        END $$
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER browser_run_terminal_immutable BEFORE UPDATE OR DELETE ON browser_runs
        FOR EACH ROW EXECUTE FUNCTION browser_run_terminal_immutable()
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE browser_binding_leases
        ADD COLUMN authorization_run_id TEXT,
        ADD CONSTRAINT ck_browser_lease_auth_run_identity CHECK (authorization_run_id IS NULL
        OR authorization_run_id ~ '^[A-Za-z0-9_-]{1,96}$'),
        ADD CONSTRAINT fk_browser_lease_auth_run FOREIGN KEY
        (tenant_id, ai_user_id, holder_session_id, target_system, binding_id,
        authorization_run_id)
        REFERENCES browser_runs(tenant_id, ai_user_id, session_id, target_system, binding_id,
        run_id)
        ON DELETE RESTRICT,
        DROP CONSTRAINT ck_browser_lease_claim,
        ADD CONSTRAINT ck_browser_lease_claim CHECK (
        (state IN ('held', 'quarantined') AND capacity_held
        AND lease_epoch>0 AND lease_revision>0
        AND holder_id IS NOT NULL AND holder_session_id IS NOT NULL
        AND binding_revision IS NOT NULL
        AND ((authorization_revision IS NOT NULL AND authorization_run_id IS NULL)
        OR (authorization_revision IS NULL AND authorization_run_id IS NOT NULL))
        AND auth_session_fingerprint IS NOT NULL AND auth_expires_at IS NOT NULL
        AND deadline IS NOT NULL AND provider_key IS NOT NULL
        AND acquisition_operation_id IS NOT NULL)
        OR (state='released' AND NOT capacity_held
        AND holder_id IS NULL AND holder_session_id IS NULL
        AND binding_revision IS NULL AND authorization_revision IS NULL
        AND authorization_run_id IS NULL
        AND auth_session_fingerprint IS NULL AND auth_expires_at IS NULL
        AND deadline IS NULL AND provider_key IS NULL
        AND acquisition_operation_id IS NULL AND resource_cipher_version IS NULL
        AND acquisition_phase='reservation_only' AND NOT acquisition_send_started
        AND resource_proof_digest IS NULL
        AND (release_outcome IS NULL OR release_outcome IN ('released', 'terminated'))))
    """)
    )


def downgrade() -> None:
    op.execute(
        sa.text("""
        DO $$ BEGIN
        IF NOT pg_try_advisory_xact_lock(746420211000)
        THEN RAISE EXCEPTION 'browser_lease_writers_active'; END IF;
        IF NOT pg_try_advisory_xact_lock(746420212000)
        THEN RAISE EXCEPTION 'browser_vertical_writers_active'; END IF;
        END $$
    """)
    )
    op.execute(
        sa.text("""
        LOCK TABLE tasks, task_events, browser_runs, browser_profiles,
        browser_profile_generations,
        browser_publications, browser_binding_leases IN ACCESS EXCLUSIVE MODE
    """)
    )
    op.execute(
        sa.text("""
        DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM browser_runs)
        OR EXISTS(SELECT 1 FROM browser_profiles)
        OR EXISTS(SELECT 1 FROM browser_profile_generations)
        OR EXISTS(SELECT 1 FROM browser_publications)
        OR EXISTS(SELECT 1 FROM tasks WHERE client_request_id IS NOT NULL
        OR request_schema_version IS NOT NULL OR request_digest IS NOT NULL
        OR request_digest_key_id IS NOT NULL
        OR processing_owner IS NOT NULL OR processing_deadline IS NOT NULL)
        OR EXISTS(SELECT 1 FROM browser_binding_leases WHERE capacity_held OR state<>'released')
        THEN RAISE EXCEPTION 'browser_vertical_downgrade_requires_empty_history'; END IF;
        END $$
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE browser_binding_leases
        DROP CONSTRAINT fk_browser_lease_auth_run,
        DROP CONSTRAINT ck_browser_lease_auth_run_identity,
        DROP CONSTRAINT ck_browser_lease_claim,
        DROP COLUMN authorization_run_id,
        ADD CONSTRAINT ck_browser_lease_claim CHECK (
        (state IN ('held', 'quarantined') AND capacity_held
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
        AND (release_outcome IS NULL OR release_outcome IN ('released', 'terminated'))))
    """)
    )
    op.execute(
        sa.text("""
        DROP TABLE browser_runs
    """)
    )
    op.execute(
        sa.text("""
        DROP FUNCTION browser_run_terminal_immutable()
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE browser_profiles DROP CONSTRAINT fk_browser_profile_active_generation
    """)
    )
    op.execute(
        sa.text("""
        DROP TABLE browser_profile_generations
    """)
    )
    op.execute(
        sa.text("""
        DROP FUNCTION browser_profile_generation_immutable()
    """)
    )
    op.execute(
        sa.text("""
        DROP TABLE browser_profiles
    """)
    )
    op.execute(
        sa.text("""
        DROP FUNCTION browser_profile_head_monotonic()
    """)
    )
    op.execute(
        sa.text("""
        DROP TABLE browser_publications
    """)
    )
    op.execute(
        sa.text("""
        DROP FUNCTION browser_publication_content_immutable()
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE task_events DROP CONSTRAINT uq_browser_task_event_parent
    """)
    )
    op.execute(
        sa.text("""
        DROP INDEX uq_browser_task_request
    """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE tasks DROP CONSTRAINT uq_browser_task_owner,
        DROP CONSTRAINT ck_browser_task_processing, DROP CONSTRAINT ck_browser_task_request,
        DROP COLUMN processing_deadline, DROP COLUMN processing_owner, DROP COLUMN
        request_digest_key_id,
        DROP COLUMN request_digest,
        DROP COLUMN client_request_id, DROP COLUMN request_schema_version
    """)
    )
