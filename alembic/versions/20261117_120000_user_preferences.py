"""Per-user appearance and home layout; keyed by the session principal only."""

from alembic import op

revision = "20261117_120000"
down_revision = "20261106_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE user_preferences (
    tenant_id TEXT NOT NULL,
    ai_user_id TEXT NOT NULL,
    appearance JSONB,
    home_layout JSONB,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, ai_user_id)
)""")


def downgrade() -> None:
    op.execute("DROP TABLE user_preferences")
