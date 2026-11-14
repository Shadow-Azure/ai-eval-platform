"""M8-25: per-case dataset environment image.

Revision ID: 0024_dataset_case_environment_image
Revises: 0023_agent_asset_load_test
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_dataset_case_image"
down_revision = "0023_agent_asset_load_test"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dataset_case",
        sa.Column("environment_image", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_case", "environment_image")
