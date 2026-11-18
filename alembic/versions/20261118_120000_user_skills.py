"""Personal saved read flows: owner-scoped head rows and immutable versions."""

import sqlalchemy as sa

from alembic import op

revision = "20261118_120000"
down_revision = "20261117_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE user_skills (
    tenant_id TEXT NOT NULL CHECK (length(btrim(tenant_id)) > 0),
    skill_id UUID NOT NULL,
    owner_user_id TEXT NOT NULL CHECK (length(btrim(owner_user_id)) > 0),
    display_name TEXT NOT NULL CHECK (char_length(btrim(display_name)) BETWEEN 1 AND 40),
    state TEXT NOT NULL CHECK (state IN ('usable', 'paused', 'disabled')),
    pause_reason TEXT CHECK (
        pause_reason IS NULL OR pause_reason IN ('capability_changed', 'capability_unavailable')
    ),
    current_version INTEGER NOT NULL CHECK (current_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, skill_id),
    CHECK ((state = 'paused') = (pause_reason IS NOT NULL))
)""")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_skills_owner_name "
        "ON user_skills (tenant_id, owner_user_id, lower(btrim(display_name)))"
    )
    op.execute(
        "CREATE INDEX ix_user_skills_owner_state ON user_skills (tenant_id, owner_user_id, state)"
    )
    op.execute("""CREATE TABLE user_skill_versions (
    tenant_id TEXT NOT NULL,
    skill_id UUID NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    definition JSONB NOT NULL CHECK (
        jsonb_typeof(definition) = 'object'
        AND (definition->>'format') IS NOT DISTINCT FROM 'user_skill.v1'
    ),
    definition_digest BYTEA NOT NULL CHECK (octet_length(definition_digest) = 32),
    purpose TEXT NOT NULL CHECK (char_length(purpose) BETWEEN 1 AND 120),
    business_rules TEXT NOT NULL CHECK (char_length(business_rules) BETWEEN 1 AND 300),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, skill_id, version),
    FOREIGN KEY (tenant_id, skill_id) REFERENCES user_skills (tenant_id, skill_id)
        ON DELETE RESTRICT
)""")
    op.execute("""CREATE FUNCTION user_skill_versions_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'user_skill_versions rows are immutable';
END
$$""")
    op.execute(
        "CREATE TRIGGER trg_user_skill_versions_immutable BEFORE UPDATE OR DELETE "
        "ON user_skill_versions FOR EACH ROW EXECUTE FUNCTION user_skill_versions_immutable()"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE user_skill_versions, user_skills IN ACCESS EXCLUSIVE MODE")
    )
    if connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM user_skill_versions) OR EXISTS(SELECT 1 FROM user_skills)"
        )
    ).scalar_one():
        raise RuntimeError("user_skills_downgrade_irreversible")
    op.execute("DROP TRIGGER trg_user_skill_versions_immutable ON user_skill_versions")
    op.execute("DROP FUNCTION user_skill_versions_immutable()")
    op.execute("DROP TABLE user_skill_versions")
    op.execute("DROP TABLE user_skills")
