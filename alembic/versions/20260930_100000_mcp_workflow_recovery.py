"""Frozen durable MCP workflow and operation schema."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260930_100000"
down_revision = "20260930_090000"
branch_labels = None
depends_on = None

metadata = sa.MetaData()

services = sa.Table(
    "mcp_service_configs",
    metadata,
    sa.Column("tenant_id", sa.Text, primary_key=True),
    sa.Column("service_config_id", sa.Text, primary_key=True),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("config", JSONB, nullable=False),
    sa.CheckConstraint("version > 0", name="ck_mcp_service_version"),
)
registrations = sa.Table(
    "mcp_registrations",
    metadata,
    sa.Column("registration_id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("service_config_id", sa.Text, nullable=False),
    sa.Column("scope_digest", sa.Text, nullable=False, unique=True),
    sa.Column("client_id", sa.Text),
    sa.Column("state", sa.Text, nullable=False),
    sa.UniqueConstraint("registration_id", "tenant_id", "service_config_id"),
    sa.ForeignKeyConstraint(
        ["tenant_id", "service_config_id"],
        ["mcp_service_configs.tenant_id", "mcp_service_configs.service_config_id"],
    ),
    sa.CheckConstraint(
        "state IN ('REGISTERING','REGISTERED','UNKNOWN')", name="ck_mcp_registration"
    ),
)
connections = sa.Table(
    "mcp_connections",
    metadata,
    sa.Column("connection_id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("user_id", sa.Text, nullable=False),
    sa.Column("service_config_id", sa.Text, nullable=False),
    sa.Column("service_config_version", sa.Integer, nullable=False),
    sa.Column("registration_id", sa.Text, nullable=False),
    sa.Column("binding_epoch", sa.Integer, nullable=False),
    sa.Column("grant_epoch", sa.Integer, nullable=False),
    sa.Column("login_session_fingerprint", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("identity_policy_version", sa.Text),
    sa.Column("identity_evidence", sa.Text),
    sa.Column("expires_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("tenant_id", "user_id", "service_config_id"),
    sa.UniqueConstraint("connection_id", "tenant_id", "user_id", "service_config_id"),
    sa.ForeignKeyConstraint(
        ["registration_id", "tenant_id", "service_config_id"],
        [
            "mcp_registrations.registration_id",
            "mcp_registrations.tenant_id",
            "mcp_registrations.service_config_id",
        ],
    ),
    sa.CheckConstraint("binding_epoch > 0 AND grant_epoch >= 0", name="ck_mcp_epochs"),
    sa.CheckConstraint(
        "state IN ('AUTHORIZING','PENDING_IDENTITY','ACTIVE','DISCONNECTED')",
        name="ck_mcp_connection_state",
    ),
)
transactions = sa.Table(
    "mcp_oauth_transactions",
    metadata,
    sa.Column("state_digest", sa.Text, primary_key=True),
    sa.Column("connection_id", sa.Text, nullable=False),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("user_id", sa.Text, nullable=False),
    sa.Column("service_config_id", sa.Text, nullable=False),
    sa.Column("binding_epoch", sa.Integer, nullable=False),
    sa.Column("login_session_fingerprint", sa.Text, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consumed", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("encrypted_payload", sa.LargeBinary, nullable=False),
    sa.ForeignKeyConstraint(
        ["connection_id", "tenant_id", "user_id", "service_config_id"],
        [
            "mcp_connections.connection_id",
            "mcp_connections.tenant_id",
            "mcp_connections.user_id",
            "mcp_connections.service_config_id",
        ],
    ),
)
grants = sa.Table(
    "mcp_grants",
    metadata,
    sa.Column("connection_id", sa.Text, primary_key=True),
    sa.Column("grant_epoch", sa.Integer, primary_key=True),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("user_id", sa.Text, nullable=False),
    sa.Column("service_config_id", sa.Text, nullable=False),
    sa.Column("binding_epoch", sa.Integer, nullable=False),
    sa.Column("login_session_fingerprint", sa.Text, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("encrypted_payload", sa.LargeBinary, nullable=False),
    sa.ForeignKeyConstraint(
        ["connection_id", "tenant_id", "user_id", "service_config_id"],
        [
            "mcp_connections.connection_id",
            "mcp_connections.tenant_id",
            "mcp_connections.user_id",
            "mcp_connections.service_config_id",
        ],
    ),
)
mappings = sa.Table(
    "mcp_capability_bindings",
    metadata,
    sa.Column("capability_id", sa.Text, primary_key=True),
    sa.Column("capability_version", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("service_config_id", sa.Text, nullable=False),
    sa.Column("binding", JSONB, nullable=False),
    sa.ForeignKeyConstraint(
        ["tenant_id", "service_config_id"],
        ["mcp_service_configs.tenant_id", "mcp_service_configs.service_config_id"],
    ),
)

workflow_runs = sa.Table(
    "mcp_workflow_runs",
    metadata,
    sa.Column("operation_id", sa.Text, primary_key=True),
    sa.Column("task_id", sa.Text, nullable=False, unique=True),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("user_id", sa.Text, nullable=False),
    sa.Column("service_config_id", sa.Text, nullable=False),
    sa.Column("connection_id", sa.Text, nullable=False),
    sa.Column("registration_id", sa.Text, nullable=False),
    sa.Column("checkpoint", JSONB, nullable=False),
    sa.UniqueConstraint("operation_id", "tenant_id", "user_id", "service_config_id"),
    sa.ForeignKeyConstraint(
        ["connection_id", "tenant_id", "user_id", "service_config_id"],
        [
            "mcp_connections.connection_id",
            "mcp_connections.tenant_id",
            "mcp_connections.user_id",
            "mcp_connections.service_config_id",
        ],
    ),
    sa.ForeignKeyConstraint(
        ["registration_id", "tenant_id", "service_config_id"],
        [
            "mcp_registrations.registration_id",
            "mcp_registrations.tenant_id",
            "mcp_registrations.service_config_id",
        ],
    ),
)
operations = sa.Table(
    "mcp_operations",
    metadata,
    sa.Column("operation_id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, nullable=False),
    sa.Column("user_id", sa.Text, nullable=False),
    sa.Column("service_config_id", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("attempt_id", sa.Text),
    sa.Column("send_started", sa.Boolean, nullable=False),
    sa.Column("encrypted_payload", sa.LargeBinary, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["operation_id", "tenant_id", "user_id", "service_config_id"],
        [
            "mcp_workflow_runs.operation_id",
            "mcp_workflow_runs.tenant_id",
            "mcp_workflow_runs.user_id",
            "mcp_workflow_runs.service_config_id",
        ],
    ),
    sa.CheckConstraint("revision > 0", name="ck_mcp_operation_revision"),
    sa.CheckConstraint(
        "state IN ('READY','WAITING_LOCAL_CONFIRM','WAITING_EXTERNAL_CONFIRM',"
        "'SENDING','UNKNOWN','VERIFIED_SUCCESS','FAILED','CANCELLED','EXPIRED')",
        name="ck_mcp_operation_state",
    ),
)


def upgrade() -> None:
    workflow_runs.create(op.get_bind())
    operations.create(op.get_bind())


def downgrade() -> None:
    conn = op.get_bind()
    for table in (operations, workflow_runs):
        if conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one():
            raise RuntimeError("mcp_downgrade_refused: recovery records remain")
    operations.drop(conn)
    workflow_runs.drop(conn)
