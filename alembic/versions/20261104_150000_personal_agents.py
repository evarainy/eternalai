"""Personal published-text agents, grants and atomic IM Inbox; authored only."""

import sqlalchemy as sa

from alembic import op

revision = "20261104_150000"
down_revision = "20261104_130000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE personal_agents (
    agent_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_binding_id UUID NOT NULL REFERENCES im_account_bindings(binding_id),
    owner_binding_version BYTEA NOT NULL,
    provider TEXT NOT NULL,
    im_instance_id TEXT NOT NULL,
    realm_id TEXT NOT NULL,
    bot_user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','disabled')),
    UNIQUE (tenant_id, owner_binding_id),
    UNIQUE (provider, im_instance_id, realm_id, bot_user_id)
)""")
    op.execute("""CREATE TABLE personal_agent_materials (
    agent_id UUID NOT NULL REFERENCES personal_agents(agent_id),
    version BIGINT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    content_sha256 BYTEA NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    withdrawn_at TIMESTAMPTZ,
    PRIMARY KEY (agent_id, version)
)""")
    op.execute("""CREATE TABLE personal_agent_grants (
    grant_id UUID PRIMARY KEY,
    agent_id UUID NOT NULL,
    material_version BIGINT NOT NULL,
    grantee_binding_id UUID NOT NULL REFERENCES im_account_bindings(binding_id),
    grantee_binding_version BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    FOREIGN KEY (agent_id, material_version)
        REFERENCES personal_agent_materials(agent_id, version)
)""")
    op.execute("""CREATE UNIQUE INDEX ux_personal_agent_current_grant
    ON personal_agent_grants (agent_id, grantee_binding_id)
    WHERE revoked_at IS NULL""")
    op.execute("""CREATE TABLE im_inbox (
    inbox_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    im_instance_id TEXT NOT NULL,
    realm_id TEXT NOT NULL,
    target_bot_id TEXT NOT NULL,
    agent_id UUID NOT NULL REFERENCES personal_agents(agent_id),
    source_message_id TEXT NOT NULL,
    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('direct_message','mention')),
    payload_fingerprint BYTEA NOT NULL,
    actor_binding_id UUID NOT NULL REFERENCES im_account_bindings(binding_id),
    actor_binding_version BYTEA NOT NULL,
    conversation_ref TEXT NOT NULL,
    conversation_kind TEXT NOT NULL CHECK (conversation_kind IN ('private','group')),
    received_at TIMESTAMPTZ NOT NULL,
    queue_job_id BIGINT NOT NULL UNIQUE,
    grant_id UUID REFERENCES personal_agent_grants(grant_id),
    state TEXT NOT NULL CHECK (state IN (
        'queued','running','completed','refused','failed'
    )),
    result_code TEXT,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (tenant_id, provider, im_instance_id, realm_id,
            target_bot_id, source_message_id, trigger_kind)
)""")
    op.execute("""CREATE INDEX ix_im_inbox_actor
    ON im_inbox (tenant_id, actor_binding_id, received_at DESC)""")
    op.execute("""ALTER TABLE im_deliveries
    ADD COLUMN origin_inbox_id UUID REFERENCES im_inbox(inbox_id),
    ADD COLUMN reply_slot TEXT,
    ALTER COLUMN recipient_ai_user_id DROP NOT NULL,
    ALTER COLUMN recipient_binding_id DROP NOT NULL,
    ALTER COLUMN recipient_mapping_version DROP NOT NULL,
    ALTER COLUMN confirmation_sha256 DROP NOT NULL,
    ALTER COLUMN send_expires_at DROP NOT NULL""")
    op.execute("""ALTER TABLE im_deliveries
    DROP CONSTRAINT ck_im_delivery_confirmed_enqueued""")
    op.execute("""ALTER TABLE im_deliveries ADD CONSTRAINT ck_im_delivery_origin CHECK (
    (origin_inbox_id IS NULL AND reply_slot IS NULL
        AND recipient_ai_user_id IS NOT NULL
        AND recipient_binding_id IS NOT NULL
        AND recipient_mapping_version IS NOT NULL
        AND confirmation_sha256 IS NOT NULL
        AND send_expires_at IS NOT NULL
        AND (state NOT IN ('ready','sending','accepted','known_rejected','unknown')
             OR (confirmed_at IS NOT NULL AND queue_job_id IS NOT NULL)))
    OR
    (origin_inbox_id IS NOT NULL AND reply_slot IS NOT NULL
        AND reply_slot IN ('private','group_status')
        AND confirmed_at IS NULL AND confirmation_sha256 IS NULL
        AND send_expires_at IS NULL
        AND queue_job_id IS NOT NULL
        AND ((reply_slot='private' AND recipient_ai_user_id IS NOT NULL
              AND recipient_binding_id IS NOT NULL
              AND recipient_mapping_version IS NOT NULL)
             OR (reply_slot='group_status' AND recipient_ai_user_id IS NULL
                 AND recipient_binding_id IS NULL
                 AND recipient_mapping_version IS NULL)))
)""")
    op.execute("""CREATE UNIQUE INDEX ux_im_delivery_inbox_reply
    ON im_deliveries (origin_inbox_id, reply_slot)
    WHERE origin_inbox_id IS NOT NULL""")


def downgrade() -> None:
    # Required CI may round-trip an empty V3 schema. Published material or any
    # V3 history must be retained; a real downgrade needs separate authorization.
    connection = op.get_bind()
    connection.execute(sa.text(
        "LOCK TABLE personal_agents, personal_agent_materials, personal_agent_grants, "
        "im_inbox, im_deliveries IN ACCESS EXCLUSIVE MODE"
    ))
    if connection.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM personal_agents) "
        "OR EXISTS(SELECT 1 FROM personal_agent_materials) "
        "OR EXISTS(SELECT 1 FROM personal_agent_grants) "
        "OR EXISTS(SELECT 1 FROM im_inbox)"
    )).scalar_one():
        raise RuntimeError("personal_agents_downgrade_requires_explicit_retention_plan")
    op.execute("DROP INDEX ux_im_delivery_inbox_reply")
    op.execute("ALTER TABLE im_deliveries DROP CONSTRAINT ck_im_delivery_origin")
    op.execute("""ALTER TABLE im_deliveries
        DROP COLUMN origin_inbox_id,
        DROP COLUMN reply_slot,
        ALTER COLUMN recipient_ai_user_id SET NOT NULL,
        ALTER COLUMN recipient_binding_id SET NOT NULL,
        ALTER COLUMN recipient_mapping_version SET NOT NULL,
        ALTER COLUMN confirmation_sha256 SET NOT NULL,
        ALTER COLUMN send_expires_at SET NOT NULL""")
    op.execute("""ALTER TABLE im_deliveries
        ADD CONSTRAINT ck_im_delivery_confirmed_enqueued CHECK
        (state NOT IN ('ready','sending','accepted','known_rejected','unknown')
         OR (confirmed_at IS NOT NULL AND queue_job_id IS NOT NULL))""")
    op.drop_table("im_inbox")
    op.drop_table("personal_agent_grants")
    op.drop_table("personal_agent_materials")
    op.drop_table("personal_agents")
