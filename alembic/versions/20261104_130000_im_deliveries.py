"""Text-delivery Outbox and explicit recipient verification history.

Authored only. Before execution, integrate V1's im_account_bindings migration and
adapt the parent to the then-unique head. No file-artifact dependency is present.
"""

from alembic import op

revision = "20261104_130000"
down_revision = "20261103_120100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE im_deliveries (
    delivery_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    initiator_ai_user_id TEXT NOT NULL,
    recipient_ai_user_id TEXT NOT NULL,
    recipient_binding_id UUID NOT NULL REFERENCES im_account_bindings(binding_id),
    recipient_mapping_version BYTEA NOT NULL,
    provider TEXT NOT NULL,
    im_instance_id TEXT NOT NULL,
    realm_id TEXT NOT NULL,
    sender_bot_id TEXT NOT NULL,
    target_conversation_ref TEXT NOT NULL,
    card_content TEXT NOT NULL,
    note TEXT NOT NULL,
    confirmation_sha256 BYTEA NOT NULL,
    idempotency_key UUID NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'prepared', 'ready', 'sending', 'accepted', 'known_rejected',
        'unknown', 'cancelled', 'expired'
    )),
    created_at TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ,
    send_expires_at TIMESTAMPTZ NOT NULL,
    queue_job_id BIGINT UNIQUE,
    send_attempt_id UUID UNIQUE,
    attempt_started_at TIMESTAMPTZ,
    remote_message_id TEXT,
    accepted_at TIMESTAMPTZ,
    result_code TEXT,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_im_delivery_request UNIQUE
        (tenant_id, initiator_ai_user_id, idempotency_key),
    CONSTRAINT ck_im_delivery_confirmed_enqueued CHECK
        (state NOT IN ('ready','sending','accepted','known_rejected','unknown')
         OR (confirmed_at IS NOT NULL AND queue_job_id IS NOT NULL))
);
""")
    op.execute("""CREATE INDEX ix_im_deliveries_initiator
    ON im_deliveries (tenant_id, initiator_ai_user_id, created_at DESC)""")
    op.execute("""CREATE INDEX ix_im_deliveries_recipient
    ON im_deliveries (tenant_id, recipient_ai_user_id, created_at DESC)""")
    op.execute("""CREATE INDEX ix_im_deliveries_pending
    ON im_deliveries (state, updated_at)
    WHERE state IN ('ready','sending','unknown')""")
    op.execute("""CREATE UNIQUE INDEX ux_im_delivery_remote_message
    ON im_deliveries (provider, im_instance_id, realm_id, remote_message_id)
    WHERE remote_message_id IS NOT NULL""")
    op.execute("""CREATE TABLE im_verifications (
    verification_id UUID PRIMARY KEY,
    delivery_id UUID NOT NULL REFERENCES im_deliveries(delivery_id),
    result TEXT NOT NULL CHECK (result IN ('verified','needs_changes')),
    comment TEXT NOT NULL,
    idempotency_key UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_im_verification_request UNIQUE (delivery_id, idempotency_key)
);
""")
    op.execute("""CREATE INDEX ix_im_verifications_delivery
    ON im_verifications (delivery_id, created_at, verification_id)""")


def downgrade() -> None:
    raise RuntimeError("Disable IM delivery and retain its history; downgrade is not supported")
