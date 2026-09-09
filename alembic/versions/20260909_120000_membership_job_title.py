"""Mirror the approved OA jobtitle field; snapshot import repopulates it after downgrade."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260909_120000"
down_revision = "20260901_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organization_user_memberships", sa.Column("job_title", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("organization_user_memberships", "job_title")
