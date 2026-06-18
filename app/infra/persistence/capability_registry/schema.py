"""SQLAlchemy Core schema for capability registry persistence."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()

capabilities = sa.Table(
    "capabilities",
    metadata,
    sa.Column("capability_id", sa.Text(), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("type", sa.Text(), nullable=False),
    sa.Column(
        "intent_tags",
        JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
    sa.Column(
        "input_schema",
        JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    ),
    sa.Column(
        "output_schema",
        JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    ),
    sa.Column("input_schema_digest", sa.Text(), nullable=False),
    sa.Column("output_schema_digest", sa.Text(), nullable=False),
    sa.Column("risk_level", sa.Text(), nullable=False),
    sa.Column("owner", sa.Text(), nullable=False),
    sa.Column("version", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("short_description", sa.Text(), nullable=False),
    sa.Column("target_system", sa.Text(), nullable=True),
    sa.Column("execution_identity", sa.Text(), nullable=False),
    sa.Column("binding_required", sa.Boolean(), nullable=False),
    sa.Column("policy_digest", sa.Text(), nullable=True),
    sa.CheckConstraint(
        "type IN ('query','action','workflow','mock')",
        name="ck_capabilities_type",
    ),
    sa.CheckConstraint(
        "risk_level IN ('low','medium','high')",
        name="ck_capabilities_risk_level",
    ),
    sa.CheckConstraint(
        "status IN ('draft','active','disabled','deprecated')",
        name="ck_capabilities_status",
    ),
    sa.CheckConstraint(
        "target_system IS NULL OR target_system IN ('oa','u8','hikvision_ivms')",
        name="ck_capabilities_target_system",
    ),
    sa.CheckConstraint(
        "execution_identity IN ('user_delegated','system_scope','admin_approved_proxy')",
        name="ck_capabilities_execution_identity",
    ),
)
