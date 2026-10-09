"""Separate immutable Profile audit facts from individually reclaimable ciphertext.

This migration moves existing encrypted bytes in one PostgreSQL transaction.
It does not erase old heap pages, WAL, replicas or backups. Downgrade is refused
once a generation has been cleaned because its ciphertext cannot be recovered.
"""

import sqlalchemy as sa

from alembic import op

revision = "20261009_120000"
down_revision = "20261003_212000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "LOCK TABLE browser_profile_generations IN ACCESS EXCLUSIVE MODE"
    ))
    op.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM browser_profile_generations
                     WHERE disposition='cleanup_confirmed') THEN
            RAISE EXCEPTION 'browser_profile_legacy_cleanup_unproven';
          END IF;
        END $$
    """))
    op.execute(sa.text("""
        CREATE TABLE browser_profile_payloads (
          tenant_id TEXT NOT NULL,
          ai_user_id TEXT NOT NULL,
          session_id TEXT NOT NULL,
          target_system TEXT NOT NULL,
          binding_id TEXT NOT NULL,
          generation_id TEXT NOT NULL,
          cipher_version TEXT NOT NULL CHECK(cipher_version='aes256gcm-browser-profile-v1'),
          key_id TEXT NOT NULL CHECK(length(btrim(key_id))>0),
          nonce BYTEA NOT NULL CHECK(octet_length(nonce)=12),
          ciphertext BYTEA NOT NULL CHECK(octet_length(ciphertext)>=16),
          PRIMARY KEY(tenant_id,ai_user_id,session_id,target_system,binding_id,generation_id),
          FOREIGN KEY(tenant_id,ai_user_id,session_id,target_system,binding_id,generation_id)
            REFERENCES browser_profile_generations
              (tenant_id,ai_user_id,session_id,target_system,binding_id,generation_id)
            ON DELETE RESTRICT
        )
    """))
    op.execute(sa.text("""
        INSERT INTO browser_profile_payloads
          (tenant_id,ai_user_id,session_id,target_system,binding_id,generation_id,
           cipher_version,key_id,nonce,ciphertext)
        SELECT tenant_id,ai_user_id,session_id,target_system,binding_id,generation_id,
               cipher_version,key_id,nonce,ciphertext
        FROM browser_profile_generations
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
          IF (SELECT count(*) FROM browser_profile_payloads) <>
             (SELECT count(*) FROM browser_profile_generations) THEN
            RAISE EXCEPTION 'browser_profile_payload_migration_incomplete';
          END IF;
        END $$
    """))
    op.execute(sa.text("""
        ALTER TABLE browser_profile_generations
          DROP COLUMN cipher_version, DROP COLUMN key_id,
          DROP COLUMN nonce, DROP COLUMN ciphertext,
          ADD COLUMN disposition_changed_at TIMESTAMPTZ
            CHECK(disposition_changed_at IS NULL OR isfinite(disposition_changed_at))
    """))
    # A later head timestamp is a conservative lower bound for historical
    # retired generations. Historical orphans have no trustworthy bound.
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION browser_profile_generation_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'browser_profile_generation_delete_forbidden';
          END IF;
          IF (to_jsonb(NEW)-ARRAY['disposition','cleanup_proof_digest',
                                  'disposition_changed_at']) IS DISTINCT FROM
             (to_jsonb(OLD)-ARRAY['disposition','cleanup_proof_digest',
                                  'disposition_changed_at']) THEN
            RAISE EXCEPTION 'browser_profile_generation_immutable';
          END IF;
          IF NOT ((OLD.disposition='validated' AND NEW.disposition IN ('promoted','orphan'))
              OR (OLD.disposition='promoted' AND NEW.disposition='retired')
              OR (OLD.disposition IN ('retired','orphan')
                  AND NEW.disposition='cleanup_confirmed')) THEN
            RAISE EXCEPTION 'browser_profile_generation_transition_invalid';
          END IF;
          IF NEW.disposition='cleanup_confirmed' AND
             (NEW.cleanup_proof_digest IS NULL OR OLD.cleanup_proof_digest IS NOT NULL) THEN
            RAISE EXCEPTION 'browser_profile_cleanup_proof_invalid';
          END IF;
          NEW.disposition_changed_at := clock_timestamp();
          RETURN NEW;
        END $$
    """))
    op.execute(sa.text("""
        CREATE FUNCTION browser_profile_generation_insert_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.disposition<>'validated' OR NEW.cleanup_proof_digest IS NOT NULL OR
             NEW.disposition_changed_at IS NOT NULL THEN
            RAISE EXCEPTION 'browser_profile_generation_insert_invalid';
          END IF;
          RETURN NEW;
        END $$
    """))
    op.execute(sa.text("""
        CREATE TRIGGER browser_profile_generation_insert_guard
        BEFORE INSERT ON browser_profile_generations
        FOR EACH ROW EXECUTE FUNCTION browser_profile_generation_insert_guard()
    """))
    op.execute(sa.text("""
        CREATE FUNCTION browser_profile_payload_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE state TEXT; target_id TEXT;
        BEGIN
          IF TG_OP='UPDATE' THEN
            RAISE EXCEPTION 'browser_profile_payload_immutable';
          END IF;
          IF TG_OP='INSERT' THEN
            target_id := NEW.generation_id;
          ELSE
            target_id := OLD.generation_id;
          END IF;
          SELECT disposition INTO state FROM browser_profile_generations
          WHERE generation_id=target_id
          FOR UPDATE;
          IF state IS NULL OR
             (TG_OP='INSERT' AND state='cleanup_confirmed') OR
             (TG_OP='DELETE' AND state<>'cleanup_confirmed') THEN
            RAISE EXCEPTION 'browser_profile_payload_state_invalid';
          END IF;
          IF TG_OP='INSERT' THEN RETURN NEW; END IF;
          RETURN OLD;
        END $$
    """))
    op.execute(sa.text("""
        CREATE TRIGGER browser_profile_payload_guard
        BEFORE INSERT OR UPDATE OR DELETE ON browser_profile_payloads
        FOR EACH ROW EXECUTE FUNCTION browser_profile_payload_guard()
    """))
    op.execute(sa.text("""
        CREATE TABLE browser_profile_cleanup_receipts (
          tenant_id TEXT NOT NULL,
          ai_user_id TEXT NOT NULL,
          session_id TEXT NOT NULL,
          target_system TEXT NOT NULL,
          binding_id TEXT NOT NULL,
          generation_id TEXT NOT NULL,
          provider_key TEXT NOT NULL CHECK(provider_key ~ '^[A-Za-z0-9_-]{1,96}$'),
          capture_operation_id TEXT NOT NULL
            CHECK(capture_operation_id ~ '^[A-Za-z0-9_-]{1,96}$'),
          generation_ref_digest BYTEA NOT NULL CHECK(octet_length(generation_ref_digest)=32),
          proof_digest BYTEA NOT NULL CHECK(octet_length(proof_digest)=32),
          recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
            CHECK(isfinite(recorded_at)),
          PRIMARY KEY(tenant_id,ai_user_id,session_id,target_system,binding_id,generation_id),
          FOREIGN KEY(tenant_id,ai_user_id,session_id,target_system,binding_id,generation_id)
            REFERENCES browser_profile_generations
              (tenant_id,ai_user_id,session_id,target_system,binding_id,generation_id)
            ON DELETE RESTRICT
        )
    """))
    op.execute(sa.text("""
        CREATE FUNCTION browser_profile_cleanup_receipt_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE stored RECORD;
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'browser_profile_cleanup_receipt_immutable';
          END IF;
          SELECT disposition,provider_key,capture_operation_id,generation_ref_digest
            INTO stored FROM browser_profile_generations
            WHERE generation_id=NEW.generation_id
              AND tenant_id=NEW.tenant_id AND ai_user_id=NEW.ai_user_id
              AND session_id=NEW.session_id AND target_system=NEW.target_system
              AND binding_id=NEW.binding_id FOR UPDATE;
          IF NOT FOUND OR stored.disposition='cleanup_confirmed' OR
             stored.provider_key<>NEW.provider_key OR
             stored.capture_operation_id<>NEW.capture_operation_id OR
             stored.generation_ref_digest<>NEW.generation_ref_digest THEN
            RAISE EXCEPTION 'browser_profile_cleanup_receipt_invalid';
          END IF;
          RETURN NEW;
        END $$
    """))
    op.execute(sa.text("""
        CREATE TRIGGER browser_profile_cleanup_receipt_guard
        BEFORE INSERT OR UPDATE OR DELETE ON browser_profile_cleanup_receipts
        FOR EACH ROW EXECUTE FUNCTION browser_profile_cleanup_receipt_guard()
    """))
    op.execute(sa.text("""
        CREATE FUNCTION browser_profile_payload_consistency()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE state TEXT; confirmed_proof BYTEA; receipt_proof BYTEA;
                payload_exists BOOLEAN; target_id TEXT;
        BEGIN
          IF TG_TABLE_NAME='browser_profile_generations' THEN
            target_id := NEW.generation_id;
          ELSIF TG_OP='DELETE' THEN
            target_id := OLD.generation_id;
          ELSE
            target_id := NEW.generation_id;
          END IF;
          SELECT disposition,cleanup_proof_digest INTO state,confirmed_proof
            FROM browser_profile_generations
            WHERE generation_id=target_id;
          IF state IS NULL THEN RETURN NULL; END IF;
          SELECT EXISTS(SELECT 1 FROM browser_profile_payloads
                        WHERE generation_id=target_id) INTO payload_exists;
          IF (state='cleanup_confirmed')=payload_exists THEN
            RAISE EXCEPTION 'browser_profile_payload_consistency_invalid';
          END IF;
          IF state='cleanup_confirmed' THEN
            SELECT proof_digest INTO receipt_proof FROM browser_profile_cleanup_receipts
              WHERE generation_id=target_id;
            IF receipt_proof IS NULL OR receipt_proof<>confirmed_proof THEN
              RAISE EXCEPTION 'browser_profile_cleanup_receipt_missing';
            END IF;
          END IF;
          RETURN NULL;
        END $$
    """))
    op.execute(sa.text("""
        CREATE CONSTRAINT TRIGGER browser_profile_generation_payload_consistency
        AFTER INSERT OR UPDATE ON browser_profile_generations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION browser_profile_payload_consistency()
    """))
    op.execute(sa.text("""
        CREATE CONSTRAINT TRIGGER browser_profile_payload_consistency
        AFTER INSERT OR DELETE ON browser_profile_payloads
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION browser_profile_payload_consistency()
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "LOCK TABLE browser_profile_generations IN ACCESS EXCLUSIVE MODE"
    ))
    op.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM browser_profile_generations
                     WHERE disposition='cleanup_confirmed') OR
             EXISTS (SELECT 1 FROM browser_profile_cleanup_receipts) OR
             EXISTS (SELECT 1 FROM browser_profile_generations g
                     WHERE NOT EXISTS (SELECT 1 FROM browser_profile_payloads p
                                       WHERE p.generation_id=g.generation_id)) THEN
            RAISE EXCEPTION 'browser_profile_payload_downgrade_irreversible';
          END IF;
        END $$
    """))
    op.execute(sa.text(
        "DROP TRIGGER browser_profile_payload_consistency ON browser_profile_payloads"
    ))
    op.execute(sa.text(
        "DROP TRIGGER browser_profile_generation_payload_consistency"
        " ON browser_profile_generations"
    ))
    op.execute(sa.text("DROP FUNCTION browser_profile_payload_consistency()"))
    op.execute(sa.text(
        "DROP TRIGGER browser_profile_cleanup_receipt_guard"
        " ON browser_profile_cleanup_receipts"
    ))
    op.execute(sa.text("DROP FUNCTION browser_profile_cleanup_receipt_guard()"))
    op.execute(sa.text("DROP TABLE browser_profile_cleanup_receipts"))
    op.execute(sa.text("DROP TRIGGER browser_profile_payload_guard ON browser_profile_payloads"))
    op.execute(sa.text("DROP FUNCTION browser_profile_payload_guard()"))
    op.execute(sa.text(
        "DROP TRIGGER browser_profile_generation_insert_guard"
        " ON browser_profile_generations"
    ))
    op.execute(sa.text("DROP FUNCTION browser_profile_generation_insert_guard()"))
    # Writers cannot race the temporary trigger removal: the table is held in
    # ACCESS EXCLUSIVE mode until this entire transactional migration commits.
    op.execute(sa.text(
        "DROP TRIGGER browser_profile_generation_immutable"
        " ON browser_profile_generations"
    ))
    op.execute(sa.text("""
        ALTER TABLE browser_profile_generations
          ADD COLUMN cipher_version TEXT,
          ADD COLUMN key_id TEXT,
          ADD COLUMN nonce BYTEA,
          ADD COLUMN ciphertext BYTEA
    """))
    op.execute(sa.text("""
        UPDATE browser_profile_generations g SET
          cipher_version=p.cipher_version,key_id=p.key_id,
          nonce=p.nonce,ciphertext=p.ciphertext
        FROM browser_profile_payloads p WHERE g.generation_id=p.generation_id
    """))
    op.execute(sa.text("""
        ALTER TABLE browser_profile_generations
          ALTER COLUMN cipher_version SET NOT NULL,
          ALTER COLUMN key_id SET NOT NULL,
          ALTER COLUMN nonce SET NOT NULL,
          ALTER COLUMN ciphertext SET NOT NULL,
          ADD CHECK(cipher_version='aes256gcm-browser-profile-v1'),
          ADD CHECK(length(btrim(key_id))>0),
          ADD CHECK(octet_length(nonce)=12),
          ADD CHECK(octet_length(ciphertext)>=16)
    """))
    op.execute(sa.text("DROP TABLE browser_profile_payloads"))
    op.execute(sa.text(
        "ALTER TABLE browser_profile_generations DROP COLUMN disposition_changed_at"
    ))
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION browser_profile_generation_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'browser_profile_generation_delete_forbidden';
          END IF;
          IF (to_jsonb(NEW)-ARRAY['disposition','cleanup_proof_digest']) IS DISTINCT FROM
             (to_jsonb(OLD)-ARRAY['disposition','cleanup_proof_digest']) THEN
            RAISE EXCEPTION 'browser_profile_generation_immutable';
          END IF;
          IF NOT ((OLD.disposition='validated' AND NEW.disposition IN ('promoted','orphan'))
              OR (OLD.disposition='promoted' AND NEW.disposition='retired')
              OR (OLD.disposition IN ('retired','orphan')
                  AND NEW.disposition='cleanup_confirmed')) THEN
            RAISE EXCEPTION 'browser_profile_generation_transition_invalid';
          END IF;
          RETURN NEW;
        END $$
    """))
    op.execute(sa.text("""
        CREATE TRIGGER browser_profile_generation_immutable
        BEFORE UPDATE OR DELETE ON browser_profile_generations
        FOR EACH ROW EXECUTE FUNCTION browser_profile_generation_immutable()
    """))
