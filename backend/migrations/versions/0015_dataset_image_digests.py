"""dataset version image digest binding

Revision ID: 0015_dataset_image_digests
Revises: 0014_budget_pause_resume

M5-07: the dataset version freezes the execution and scoring image digests.
Imports may supply them explicitly; when omitted the API stores the platform
defaults. The launch wizard inherits these values after a version is selected.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_dataset_image_digests"
down_revision = "0014_budget_pause_resume"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dataset_version", sa.Column("execution_image_digest", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "dataset_version", sa.Column("scoring_image_digest", sa.String(length=128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("dataset_version", "scoring_image_digest")
    op.drop_column("dataset_version", "execution_image_digest")
