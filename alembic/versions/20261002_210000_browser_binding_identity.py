"""Add browser binding identity without reading or rewriting legacy ciphertext."""

from __future__ import annotations

from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "20261002_210000"
down_revision = "20260930_100000"
branch_labels = None
depends_on = None

_TABLE = "oa_session_credentials"
_MAX = 9007199254740991


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE oa_session_credentials IN ACCESS EXCLUSIVE MODE"))
    op.add_column(_TABLE, sa.Column("binding_id", sa.Text(), nullable=True))
    for name, default in (
        ("binding_revision", "1"),
        ("credential_write_revision", "0"),
        ("refresh_epoch", "0"),
    ):
        op.add_column(
            _TABLE, sa.Column(name, sa.BigInteger(), nullable=False, server_default=default)
        )
    op.add_column(
        _TABLE, sa.Column("binding_state", sa.Text(), nullable=False, server_default="unverified")
    )
    op.add_column(_TABLE, sa.Column("binding_subject_digest", sa.LargeBinary(), nullable=True))
    op.add_column(_TABLE, sa.Column("binding_subject_verified_at", sa.DateTime(timezone=True)))
    op.add_column(_TABLE, sa.Column("refresh_operation_id", sa.Text()))
    op.add_column(_TABLE, sa.Column("refresh_deadline", sa.DateTime(timezone=True)))
    # Only identity keys and NULL flags are selected; ciphertext never enters Python.
    rows = connection.execute(
        sa.text("SELECT tenant_id, ai_user_id, target_system FROM oa_session_credentials")
    ).mappings()
    for row in rows:
        connection.execute(
            sa.text(
                "UPDATE oa_session_credentials SET binding_id=:binding_id, binding_state=CASE"
                " WHEN revoked_at IS NOT NULL THEN 'revoked'"
                " WHEN encrypted_payload IS NULL AND encrypted_password_payload"
                " IS NULL THEN 'unbound'"
                " ELSE 'unverified' END WHERE tenant_id=:tenant_id AND ai_user_id=:ai_user_id"
                " AND target_system=:target_system"
            ),
            {**dict(row), "binding_id": uuid4().hex},
        )
    op.alter_column(_TABLE, "binding_id", nullable=False)
    op.create_unique_constraint(
        "uq_oa_credentials_binding_owner",
        _TABLE,
        ["tenant_id", "ai_user_id", "target_system", "binding_id"],
    )
    op.create_unique_constraint("uq_oa_credentials_binding_id", _TABLE, ["binding_id"])
    checks = {
        "id": "binding_id ~ '^[A-Za-z0-9_-]{1,96}$'",
        "state": "binding_state IN ('unverified','active','unbound','revoked')",
        "subject": "(binding_subject_digest IS NULL) = (binding_subject_verified_at IS NULL)"
        " AND (binding_subject_digest IS NULL OR octet_length(binding_subject_digest)=32)"
        " AND (binding_subject_verified_at IS NULL OR isfinite(binding_subject_verified_at))"
        " AND (binding_state <> 'active' OR binding_subject_digest IS NOT NULL)",
        "operation": "(refresh_operation_id IS NULL) = (refresh_deadline IS NULL)"
        " AND (refresh_operation_id IS NULL OR refresh_operation_id ~ '^[A-Za-z0-9_-]{1,96}$')"
        " AND (refresh_deadline IS NULL OR isfinite(refresh_deadline))",
    }
    for name in ("binding_revision", "credential_write_revision", "refresh_epoch"):
        minimum = 1 if name == "binding_revision" else 0
        checks[name] = f"{name} BETWEEN {minimum} AND {_MAX}"
    for name, expression in checks.items():
        op.create_check_constraint(f"ck_oa_browser_binding_{name}", _TABLE, expression)


def downgrade() -> None:
    connection = op.get_bind()
    if not connection.execute(
        sa.text("SELECT pg_try_advisory_xact_lock(746420210000)")
    ).scalar_one():
        raise RuntimeError("browser_binding_writers_active")
    connection.execute(sa.text("LOCK TABLE oa_session_credentials IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM oa_session_credentials)")
    ).scalar_one():
        raise RuntimeError("browser_binding_downgrade_requires_empty_table")
    for name in (
        "id",
        "state",
        "subject",
        "operation",
        "binding_revision",
        "credential_write_revision",
        "refresh_epoch",
    ):
        op.drop_constraint(f"ck_oa_browser_binding_{name}", _TABLE, type_="check")
    op.drop_constraint("uq_oa_credentials_binding_owner", _TABLE, type_="unique")
    op.drop_constraint("uq_oa_credentials_binding_id", _TABLE, type_="unique")
    for name in (
        "refresh_deadline",
        "refresh_operation_id",
        "refresh_epoch",
        "credential_write_revision",
        "binding_subject_verified_at",
        "binding_subject_digest",
        "binding_state",
        "binding_revision",
        "binding_id",
    ):
        op.drop_column(_TABLE, name)
