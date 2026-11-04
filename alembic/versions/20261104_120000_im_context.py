"""Verified IM identity bindings; authored only, execution requires separate authorization."""
import sqlalchemy as sa

from alembic import op

revision = "20261104_120000"
down_revision = "20261103_120100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE im_account_bindings (
    binding_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL CHECK (char_length(tenant_id) BETWEEN 1 AND 256),
    ai_user_id TEXT NOT NULL CHECK (char_length(ai_user_id) BETWEEN 1 AND 256),
    directory_user_id TEXT NOT NULL
        CHECK (char_length(directory_user_id) BETWEEN 1 AND 256),
    provider TEXT NOT NULL CHECK (char_length(provider) BETWEEN 1 AND 64),
    im_instance_id TEXT NOT NULL
        CHECK (char_length(im_instance_id) BETWEEN 1 AND 128),
    realm_id TEXT NOT NULL CHECK (char_length(realm_id) BETWEEN 1 AND 256),
    im_user_id TEXT NOT NULL CHECK (char_length(im_user_id) BETWEEN 1 AND 256),
    login_subject TEXT NOT NULL
        CHECK (char_length(login_subject) BETWEEN 1 AND 512),
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    mapping_version BIGINT NOT NULL CHECK (mapping_version > 0),
    verified_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_im_binding_host UNIQUE
        (tenant_id, ai_user_id, provider, im_instance_id, realm_id),
    CONSTRAINT uq_im_binding_directory UNIQUE
        (tenant_id, directory_user_id, provider, im_instance_id, realm_id),
    CONSTRAINT uq_im_binding_remote UNIQUE
        (provider, im_instance_id, realm_id, im_user_id),
    CONSTRAINT ck_im_binding_times CHECK (updated_at >= created_at)
);
""")


def downgrade() -> None:
    # Execution (including an empty-table downgrade) requires its own authorization.
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE im_account_bindings IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT EXISTS(SELECT 1 FROM im_account_bindings)")).scalar_one():
        raise RuntimeError("im_bindings_downgrade_irreversible")
    op.drop_table("im_account_bindings")
