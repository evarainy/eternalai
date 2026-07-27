"""Trusted-entry role and encrypted OA credential schema.

Revision ID: 20260724_090000
Revises: 20260723_090000
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260724_090000"
down_revision = "20260723_090000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "principal_roles",
        sa.Column("ai_user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("ai_user_id", "role"),
    )
    op.create_table(
        "oa_session_credentials",
        sa.Column("ai_user_id", sa.Text(), nullable=False),
        sa.Column("cipher_version", sa.Text(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ai_user_id"),
    )


def downgrade() -> None:
    op.drop_table("oa_session_credentials")
    op.drop_table("principal_roles")
