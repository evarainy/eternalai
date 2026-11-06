"""Same-version file grants on manual IM deliveries; authored only."""

from alembic import op

revision = "20261106_120000"
down_revision = "20261105_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.im_deliveries
          ADD COLUMN artifact_id UUID,
          ADD COLUMN artifact_sha256 BYTEA,
          ADD COLUMN grant_expires_at TIMESTAMPTZ,
          ADD COLUMN grant_revoked_at TIMESTAMPTZ,
          ADD CONSTRAINT fk_im_deliveries_artifact
            FOREIGN KEY (artifact_id) REFERENCES public.file_artifacts(artifact_id)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE public.im_deliveries
          DROP CONSTRAINT fk_im_deliveries_artifact,
          DROP COLUMN grant_revoked_at,
          DROP COLUMN grant_expires_at,
          DROP COLUMN artifact_sha256,
          DROP COLUMN artifact_id
    """)
