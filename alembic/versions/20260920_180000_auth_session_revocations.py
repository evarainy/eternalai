"""Persist current-session revocation without retaining bearer credentials."""

from sqlalchemy import text

from alembic import op

revision = "20260920_180000"
down_revision = "20260920_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE auth_session_revocations (
            token_fingerprint BYTEA NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            revoked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT pk_auth_session_revocations PRIMARY KEY (token_fingerprint),
            CONSTRAINT ck_auth_session_revocations_fingerprint_length
                CHECK (octet_length(token_fingerprint) = 32),
            CONSTRAINT ck_auth_session_revocations_expires_finite
                CHECK (isfinite(expires_at)),
            CONSTRAINT ck_auth_session_revocations_revoked_finite
                CHECK (isfinite(revoked_at))
        )
    """)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(text("LOCK TABLE auth_session_revocations IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        text("SELECT EXISTS (SELECT 1 FROM auth_session_revocations)")
    ).scalar_one():
        raise RuntimeError("Cannot downgrade nonempty auth_session_revocations")
    op.drop_table("auth_session_revocations")
