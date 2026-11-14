"""M8-26: dataset image source override setting.

Revision ID: 0025_dataset_image_sources
Revises: 0024_dataset_case_image
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_dataset_image_sources"
down_revision = "0024_dataset_case_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_image_sources_setting",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("sources_json", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_dataset_image_sources_setting_tenant", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_image_sources_setting"),
        sa.UniqueConstraint("tenant_id", name="uq_dataset_image_sources_setting_tenant"),
    )


def downgrade() -> None:
    op.drop_table("dataset_image_sources_setting")
