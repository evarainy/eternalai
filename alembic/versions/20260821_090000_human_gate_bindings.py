"""Add immutable Task bindings and human gate requests.

Revision ID: 20260821_090000
Revises: 20260819_120000
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260821_090000"
down_revision = "20260819_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_version_binding_manifests",
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("manifest_digest", sa.Text(), nullable=False),
        sa.Column("bindings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name="ck_task_version_binding_manifest_digest",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.task_id"],
            name="fk_task_version_binding_task_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id"),
        sa.UniqueConstraint(
            "task_id",
            "manifest_digest",
            name="uq_task_version_binding_task_digest",
        ),
    )
    op.create_table(
        "human_gate_requests",
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("requested_for_ai_user_id", sa.Text(), nullable=False),
        sa.Column("requested_session_id", sa.Text(), nullable=False),
        sa.Column("requested_tenant_id", sa.Text(), nullable=False),
        sa.Column("action_digest", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("binding_manifest_digest", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("decided_by_ai_user_id", sa.Text(), nullable=True),
        sa.Column("decided_session_id", sa.Text(), nullable=True),
        sa.Column("decided_tenant_id", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action_digest ~ '^[0-9a-f]{64}$'",
            name="ck_human_gate_action_digest",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_human_gate_request_digest",
        ),
        sa.CheckConstraint(
            "binding_manifest_digest ~ '^[0-9a-f]{64}$'",
            name="ck_human_gate_binding_digest",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('confirmed', 'rejected')",
            name="ck_human_gate_decision",
        ),
        sa.CheckConstraint(
            "(decision IS NULL AND decided_by_ai_user_id IS NULL "
            "AND decided_session_id IS NULL AND decided_tenant_id IS NULL "
            "AND decided_at IS NULL) OR (decision IS NOT NULL "
            "AND decided_by_ai_user_id IS NOT NULL AND decided_session_id IS NOT NULL "
            "AND decided_tenant_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_human_gate_decision_record",
        ),
        sa.CheckConstraint(
            "decided_by_ai_user_id IS NULL "
            "OR decided_by_ai_user_id = requested_for_ai_user_id",
            name="ck_human_gate_decision_actor",
        ),
        sa.CheckConstraint(
            "expires_at > requested_at",
            name="ck_human_gate_request_expiry",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "binding_manifest_digest"],
            [
                "task_version_binding_manifests.task_id",
                "task_version_binding_manifests.manifest_digest",
            ],
            name="fk_human_gate_task_binding",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "ix_human_gate_requests_task_id",
        "human_gate_requests",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_human_gate_requests_task_id", table_name="human_gate_requests")
    op.drop_table("human_gate_requests")
    op.drop_table("task_version_binding_manifests")
