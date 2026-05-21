"""Create pgvector extension baseline.

Revision ID: 20260521_163000
Revises:
Create Date: 2026-05-21 16:30:00
"""

from __future__ import annotations

from alembic import op


revision = "20260521_163000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
