"""Persist personal memories with tenant, department and user ownership."""

from alembic import op

revision = "20261120_140000"
down_revision = "20261120_130000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE user_memories (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    department_id TEXT NOT NULL,
    ai_user_id TEXT NOT NULL,
    category TEXT NOT NULL CONSTRAINT ck_user_memories_category
        CHECK (category IN ('answer_style', 'frequent_topic', 'work_background')),
    content TEXT NOT NULL CONSTRAINT ck_user_memories_content
        CHECK (char_length(content) BETWEEN 1 AND 200),
    normalized TEXT NOT NULL,
    source_session_id TEXT,
    source_digest TEXT NOT NULL,
    user_edited BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT uq_user_memories_owner_category_normalized
        UNIQUE (tenant_id, department_id, ai_user_id, category, normalized)
)""")
    op.execute("""CREATE INDEX ix_user_memories_owner_source
    ON user_memories (tenant_id, department_id, ai_user_id, source_digest)""")
    op.execute("""CREATE INDEX ix_user_memories_owner_live
    ON user_memories (tenant_id, department_id, ai_user_id, updated_at DESC, id DESC)
    WHERE deleted_at IS NULL""")


def downgrade() -> None:
    op.execute("DROP TABLE user_memories")
