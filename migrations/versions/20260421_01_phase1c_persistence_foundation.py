"""phase1c persistence foundation"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260421_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.Text(), primary_key=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "tasks",
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("sessions.session_id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "turns",
        sa.Column("turn_id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Text(), sa.ForeignKey("tasks.task_id"), nullable=False),
        sa.Column("user_input_type", sa.Text(), nullable=False),
        sa.Column("user_input_text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_turns_session_id_created_at", "turns", ["session_id", "created_at"])
    op.create_index("ix_tasks_session_id_created_at", "tasks", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_session_id_created_at", table_name="tasks")
    op.drop_index("ix_turns_session_id_created_at", table_name="turns")
    op.drop_table("turns")
    op.drop_table("tasks")
    op.drop_table("sessions")
