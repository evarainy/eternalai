"""Add the local read-only organization directory mirror.

Revision ID: 20260831_120000
Revises: 20260827_180000
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260831_120000"
down_revision = "20260827_180000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_departments",
        sa.Column("department_id", sa.Text(), primary_key=True),
        sa.Column("parent_department_id", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_organization_departments_parent",
        "organization_departments",
        ["parent_department_id"],
    )
    op.create_table(
        "organization_user_memberships",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("department_id", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("subcompany_id", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "department_id"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["organization_departments.department_id"]
        ),
    )
    op.create_index(
        "ix_organization_user_memberships_department",
        "organization_user_memberships",
        ["department_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organization_user_memberships_department",
        table_name="organization_user_memberships",
    )
    op.drop_table("organization_user_memberships")
    op.drop_index(
        "ix_organization_departments_parent",
        table_name="organization_departments",
    )
    op.drop_table("organization_departments")
