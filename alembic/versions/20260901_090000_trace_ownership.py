"""Add trusted tenant and user ownership to persisted Trace events.

Revision ID: 20260901_090000
Revises: 20260831_120000
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260901_090000"
down_revision = "20260831_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trace_events", sa.Column("tenant_id", sa.Text(), nullable=True))
    op.add_column("trace_events", sa.Column("ai_user_id", sa.Text(), nullable=True))
    op.execute(
        """
        WITH trusted_owners AS (
            SELECT
                h.task_id,
                h.requested_session_id AS session_id,
                MIN(h.requested_tenant_id) AS tenant_id,
                MIN(h.requested_for_ai_user_id) AS ai_user_id
            FROM human_gate_requests AS h
            JOIN tasks AS t
              ON t.task_id = h.task_id
             AND t.session_id = h.requested_session_id
             AND t.ai_user_id = h.requested_for_ai_user_id
            WHERE BTRIM(h.requested_tenant_id) <> ''
              AND BTRIM(h.requested_for_ai_user_id) <> ''
            GROUP BY h.task_id, h.requested_session_id
            HAVING COUNT(
                DISTINCT (h.requested_tenant_id, h.requested_for_ai_user_id)
            ) = 1
        )
        UPDATE trace_events AS e
           SET tenant_id = trusted_owners.tenant_id,
               ai_user_id = trusted_owners.ai_user_id
          FROM trusted_owners
         WHERE e.task_id = trusted_owners.task_id
           AND e.session_id = trusted_owners.session_id
        """
    )
    op.create_check_constraint(
        "ck_trace_events_owner_pair",
        "trace_events",
        "(tenant_id IS NULL AND ai_user_id IS NULL) OR "
        "(tenant_id IS NOT NULL AND ai_user_id IS NOT NULL "
        "AND BTRIM(tenant_id) <> '' AND BTRIM(ai_user_id) <> '')",
    )
    op.create_index(
        "ix_trace_events_tenant_id",
        "trace_events",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_trace_events_tenant_id", table_name="trace_events")
    op.drop_constraint(
        "ck_trace_events_owner_pair",
        "trace_events",
        type_="check",
    )
    op.drop_column("trace_events", "ai_user_id")
    op.drop_column("trace_events", "tenant_id")
