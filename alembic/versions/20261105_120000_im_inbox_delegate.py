"""One-hop personal-agent target on the existing Inbox; authored only."""

import sqlalchemy as sa

from alembic import op

revision = "20261105_120000"
down_revision = "20261104_150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE im_inbox ADD COLUMN delegate_agent_id UUID "
        "REFERENCES personal_agents(agent_id)"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE im_inbox IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM im_inbox WHERE delegate_agent_id IS NOT NULL)"
    )).scalar_one():
        raise RuntimeError("im_inbox_delegate_downgrade_requires_retention_plan")
    op.execute("ALTER TABLE im_inbox DROP COLUMN delegate_agent_id")
