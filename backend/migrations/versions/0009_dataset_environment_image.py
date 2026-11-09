"""dataset environment image binding

Revision ID: 0009_dataset_environment_image
Revises: 0008_service_heartbeat
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_dataset_environment_image"
down_revision = "0008_service_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dataset_version", sa.Column("environment_image", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("dataset_version", "environment_image")
