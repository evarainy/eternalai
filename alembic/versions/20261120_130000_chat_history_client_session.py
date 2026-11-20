"""Store the client session ID for resuming conversation history."""

from sqlalchemy import Column, Text

from alembic import op

revision = "20261120_130000"
down_revision = "20261120_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_conversations", Column("client_session_id", Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_conversations", "client_session_id")
