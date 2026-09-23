"""Scope sessions, roles and credentials to the explicit source tenant.

Revision ID: 20260922_190000
Revises: 20260920_180000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260922_190000"
down_revision = "20260920_180000"
branch_labels = None
depends_on = None

_TABLES = (
    ("sessions", "sessions_pkey", "ck_sessions_tenant", ["session_id"]),
    (
        "principal_roles",
        "principal_roles_pkey",
        "ck_principal_roles_tenant",
        ["ai_user_id", "role"],
    ),
    (
        "oa_session_credentials",
        "pk_oa_session_credentials",
        "ck_oa_credentials_tenant",
        ["ai_user_id", "target_system"],
    ),
)


def _lock() -> None:
    op.execute(
        "LOCK TABLE sessions, principal_roles, oa_session_credentials IN ACCESS EXCLUSIVE MODE"
    )


def upgrade() -> None:
    _lock()
    for table, primary_key, check, columns in _TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.Text(), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET tenant_id = 'default'"))
        op.alter_column(table, "tenant_id", existing_type=sa.Text(), nullable=False)
        op.create_check_constraint(check, table, "length(btrim(tenant_id)) > 0")
        op.drop_constraint(primary_key, table, type_="primary")
        op.create_primary_key(primary_key, table, ["tenant_id", *columns])
    op.create_index(
        "ix_oa_credentials_poll_tenant",
        "oa_session_credentials",
        ["tenant_id", "poll_status", "updated_at"],
    )


def downgrade() -> None:
    _lock()
    connection = op.get_bind()
    for table, _, _, _ in _TABLES:
        if connection.execute(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE tenant_id <> 'default')")
        ).scalar_one():
            raise RuntimeError("tenant_identity_downgrade_refused: non-default tenant exists")
    op.drop_index("ix_oa_credentials_poll_tenant", table_name="oa_session_credentials")
    for table, primary_key, check, columns in _TABLES:
        op.drop_constraint(primary_key, table, type_="primary")
        op.create_primary_key(primary_key, table, columns)
        op.drop_constraint(check, table, type_="check")
        op.drop_column(table, "tenant_id")
