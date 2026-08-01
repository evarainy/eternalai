"""Add the OA session credential revocation marker.

Revision ID: 20260731_090000
Revises: 20260724_090000
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260731_090000"
down_revision = "20260724_090000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oa_session_credentials",
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("oa_session_credentials", "revoked_at")
