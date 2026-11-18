"""Personal flows: persisted session state and soft deletion of saved flows."""

import sqlalchemy as sa

from alembic import op

revision = "20261118_180000"
down_revision = "20261118_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE user_skills ADD COLUMN deleted_at TIMESTAMPTZ")
    op.execute("DROP INDEX uq_user_skills_owner_name")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_skills_owner_name "
        "ON user_skills (tenant_id, owner_user_id, lower(btrim(display_name))) "
        "WHERE deleted_at IS NULL"
    )
    op.execute("""CREATE TABLE user_skill_sessions (
    tenant_id TEXT NOT NULL CHECK (length(btrim(tenant_id)) > 0),
    owner_user_id TEXT NOT NULL CHECK (length(btrim(owner_user_id)) > 0),
    session_id TEXT NOT NULL CHECK (char_length(session_id) BETWEEN 1 AND 512),
    next_seq INTEGER NOT NULL DEFAULT 1 CHECK (next_seq >= 1),
    dropped INTEGER NOT NULL DEFAULT 0 CHECK (dropped >= 0),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, owner_user_id, session_id)
)""")
    op.execute("CREATE INDEX ix_user_skill_sessions_expires ON user_skill_sessions (expires_at)")
    op.execute("""CREATE TABLE user_skill_session_items (
    tenant_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('record', 'proposal', 'trial', 'run', 'takeover')),
    item_id TEXT NOT NULL CHECK (char_length(item_id) BETWEEN 1 AND 64),
    seq INTEGER CHECK (seq >= 1),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    trial_state TEXT CHECK (trial_state IN ('ready', 'saved')),
    saved_result JSONB CHECK (jsonb_typeof(saved_result) = 'object'),
    expires_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, owner_user_id, session_id, kind, item_id),
    FOREIGN KEY (tenant_id, owner_user_id, session_id)
        REFERENCES user_skill_sessions (tenant_id, owner_user_id, session_id) ON DELETE CASCADE,
    CHECK ((kind = 'record') = (seq IS NOT NULL)),
    CHECK ((kind = 'trial') = (trial_state IS NOT NULL)),
    CHECK ((trial_state IS NOT DISTINCT FROM 'saved') = (saved_result IS NOT NULL))
)""")


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE user_skills IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM user_skills WHERE deleted_at IS NOT NULL)")
    ).scalar_one():
        raise RuntimeError("user_skills_deleted_downgrade_irreversible")
    # Session rows are short-lived working state; the approved downgrade discards them.
    op.execute("DROP TABLE user_skill_session_items")
    op.execute("DROP TABLE user_skill_sessions")
    op.execute("DROP INDEX uq_user_skills_owner_name")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_skills_owner_name "
        "ON user_skills (tenant_id, owner_user_id, lower(btrim(display_name)))"
    )
    op.execute("ALTER TABLE user_skills DROP COLUMN deleted_at")
