"""Add capability automation and Work Object handling declarations.

Revision ID: 20260827_180000
Revises: 20260827_120000
Create Date: 2026-08-27
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260827_180000"
down_revision = "20260827_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "capabilities",
        sa.Column(
            "automation_level",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
    )
    op.add_column(
        "capabilities",
        sa.Column(
            "displayable_argument_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "capabilities",
        sa.Column(
            "handles_work_objects",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_capabilities_automation_level",
        "capabilities",
        "automation_level IN ('full','assisted','manual')",
    )
    op.create_check_constraint(
        "ck_capabilities_displayable_argument_fields_is_array",
        "capabilities",
        "jsonb_typeof(displayable_argument_fields) = 'array'",
    )
    op.create_check_constraint(
        "ck_capabilities_handles_work_objects_is_array",
        "capabilities",
        "jsonb_typeof(handles_work_objects) = 'array'",
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_declarations = bool(
        connection.execute(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM capabilities "
                "WHERE automation_level <> 'manual' "
                "OR displayable_argument_fields <> '[]'::jsonb "
                "OR handles_work_objects <> '[]'::jsonb"
                ")"
            )
        ).scalar_one()
    )
    allow_destructive = os.environ.get("ALLOW_DESTRUCTIVE_DOWNGRADE") == "1"
    if has_declarations and not allow_destructive:
        raise RuntimeError(
            "表中存在能力自动化与办理映射声明，结构回滚将删除它们；确认后设置 "
            "ALLOW_DESTRUCTIVE_DOWNGRADE=1 重试"
        )

    for constraint_name in (
        "ck_capabilities_handles_work_objects_is_array",
        "ck_capabilities_displayable_argument_fields_is_array",
        "ck_capabilities_automation_level",
    ):
        op.drop_constraint(constraint_name, "capabilities", type_="check")
    for column_name in (
        "handles_work_objects",
        "displayable_argument_fields",
        "automation_level",
    ):
        op.drop_column("capabilities", column_name)
