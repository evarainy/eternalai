"""Add per-system password binding and polling state to credentials.

Revision ID: 20260821_120000
Revises: 20260821_090000
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260821_120000"
down_revision = "20260821_090000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column_name, existing_type in (
        ("cipher_version", sa.Text()),
        ("nonce", sa.LargeBinary()),
        ("encrypted_payload", sa.LargeBinary()),
        ("expires_at", sa.DateTime(timezone=True)),
    ):
        op.alter_column(
            "oa_session_credentials",
            column_name,
            existing_type=existing_type,
            nullable=True,
        )
    op.add_column(
        "oa_session_credentials",
        sa.Column(
            "target_system",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'oa'"),
        ),
    )
    op.add_column(
        "oa_session_credentials",
        sa.Column("password_cipher_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "oa_session_credentials",
        sa.Column("password_nonce", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "oa_session_credentials",
        sa.Column("encrypted_password_payload", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "oa_session_credentials",
        sa.Column(
            "poll_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'unbound'"),
        ),
    )
    op.add_column(
        "oa_session_credentials",
        sa.Column(
            "poll_failure_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("'0'"),
        ),
    )
    op.create_check_constraint(
        "ck_oa_credentials_session_fields",
        "oa_session_credentials",
        "(cipher_version IS NULL AND nonce IS NULL "
        "AND encrypted_payload IS NULL AND expires_at IS NULL) OR "
        "(cipher_version IS NOT NULL AND nonce IS NOT NULL "
        "AND encrypted_payload IS NOT NULL AND expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_oa_credentials_password_fields",
        "oa_session_credentials",
        "(password_cipher_version IS NULL AND password_nonce IS NULL "
        "AND encrypted_password_payload IS NULL) OR "
        "(password_cipher_version IS NOT NULL AND password_nonce IS NOT NULL "
        "AND encrypted_password_payload IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_oa_credentials_poll_status",
        "oa_session_credentials",
        "poll_status IN "
        "('unbound', 'active', 'retrying', 'invalid', 'captcha_required')",
    )
    op.create_check_constraint(
        "ck_oa_credentials_poll_failure_count",
        "oa_session_credentials",
        "poll_failure_count >= 0",
    )
    op.drop_constraint(
        "oa_session_credentials_pkey",
        "oa_session_credentials",
        type_="primary",
    )
    op.create_primary_key(
        "pk_oa_session_credentials",
        "oa_session_credentials",
        ["ai_user_id", "target_system"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_oa_credentials_session_fields",
        "oa_session_credentials",
        type_="check",
    )
    for column_name, existing_type in (
        ("cipher_version", sa.Text()),
        ("nonce", sa.LargeBinary()),
        ("encrypted_payload", sa.LargeBinary()),
        ("expires_at", sa.DateTime(timezone=True)),
    ):
        op.alter_column(
            "oa_session_credentials",
            column_name,
            existing_type=existing_type,
            nullable=False,
        )
    op.drop_constraint(
        "pk_oa_session_credentials",
        "oa_session_credentials",
        type_="primary",
    )
    op.create_primary_key(
        "oa_session_credentials_pkey",
        "oa_session_credentials",
        ["ai_user_id"],
    )
    op.drop_constraint(
        "ck_oa_credentials_poll_failure_count",
        "oa_session_credentials",
        type_="check",
    )
    op.drop_constraint(
        "ck_oa_credentials_poll_status",
        "oa_session_credentials",
        type_="check",
    )
    op.drop_constraint(
        "ck_oa_credentials_password_fields",
        "oa_session_credentials",
        type_="check",
    )
    op.drop_column("oa_session_credentials", "poll_failure_count")
    op.drop_column("oa_session_credentials", "poll_status")
    op.drop_column("oa_session_credentials", "encrypted_password_payload")
    op.drop_column("oa_session_credentials", "password_nonce")
    op.drop_column("oa_session_credentials", "password_cipher_version")
    op.drop_column("oa_session_credentials", "target_system")
