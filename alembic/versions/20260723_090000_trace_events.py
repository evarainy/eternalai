"""Persistent trace event schema.

Revision ID: 20260723_090000
Revises: 20260605_090000
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260723_090000"
down_revision = "20260605_090000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trace_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_trace_events_trace_id", "trace_events", ["trace_id"])
    op.create_index("ix_trace_events_task_id", "trace_events", ["task_id"])
    op.create_index("ix_trace_events_session_id", "trace_events", ["session_id"])
    op.create_index("ix_trace_events_created_at", "trace_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("trace_events")
