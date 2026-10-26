"""Per-tenant dispatch leader roster maintained by tenant administrators.

Schema only; execution requires separate environment authorization. Revocation only
marks rows, so a downgrade refuses to drop recorded roster history.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261026_120000"
down_revision = "20261022_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE dispatch_leader_roster_entries (
        entry_id UUID PRIMARY KEY,
        tenant_id TEXT NOT NULL CHECK(char_length(tenant_id)>0),
        role TEXT NOT NULL CHECK(role IN ('unit_leader','supervising_leader')),
        directory_user_id TEXT NOT NULL CHECK(char_length(directory_user_id) BETWEEN 1 AND 128),
        created_by_ai_user_id TEXT NOT NULL CHECK(char_length(created_by_ai_user_id)>0),
        created_at TIMESTAMPTZ NOT NULL,
        revoked_by_ai_user_id TEXT CHECK(char_length(revoked_by_ai_user_id)>0),
        revoked_at TIMESTAMPTZ,
        CHECK((revoked_by_ai_user_id IS NULL) = (revoked_at IS NULL)),
        CHECK(revoked_at IS NULL OR revoked_at>=created_at)
      )
    """)
    op.execute(
        "CREATE UNIQUE INDEX ux_dispatch_leader_roster_active ON "
        "dispatch_leader_roster_entries(tenant_id,role,directory_user_id) "
        "WHERE revoked_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_dispatch_leader_roster_tenant ON "
        "dispatch_leader_roster_entries(tenant_id,created_at DESC,entry_id)"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE dispatch_leader_roster_entries IN ACCESS EXCLUSIVE MODE")
    )
    if connection.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM dispatch_leader_roster_entries)")
    ).scalar_one():
        raise RuntimeError("dispatch_leader_roster_downgrade_irreversible")
    op.drop_table("dispatch_leader_roster_entries")
