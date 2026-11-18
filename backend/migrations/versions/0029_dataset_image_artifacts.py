"""dataset runtime image tarball ledger

Revision ID: 0029_dataset_image_artifacts
Revises: 0028_self_evolution
Create Date: 2026-11-18

M8 (#290): preflight/auto-prepare of the OSS ``docker save`` tarball per
dataset runtime image reference. One row per ``(tenant_id, image_ref)`` is the
idempotency anchor for the prepare worker (``status``/``next_attempt_at`` lease
plus ``attempts``) and the readiness source for prepare/create. The row also
records which public source served the pull and whether the worker removed the
local docker image and temporary tarball after a successful upload.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_dataset_image_artifacts"
down_revision: str | None = "0028_self_evolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_image_artifact",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("image_ref", sa.String(length=300), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="missing", nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=300), nullable=True),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "local_cleanup", sa.String(length=16), server_default="pending", nullable=False
        ),
        sa.Column("cleanup_error", sa.String(length=1000), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name="fk_dataset_image_artifact_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_image_artifact"),
        sa.UniqueConstraint(
            "tenant_id", "image_ref", name="uq_dataset_image_artifact_tenant_image"
        ),
        sa.CheckConstraint(
            "status IN ('missing','preparing','ready','failed')",
            name="ck_dataset_image_artifact_status",
        ),
        sa.CheckConstraint(
            "local_cleanup IN ('pending','done','failed')",
            name="ck_dataset_image_artifact_local_cleanup",
        ),
    )
    op.create_index(
        "ix_dataset_image_artifact_status_next",
        "dataset_image_artifact",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_dataset_image_artifact_tenant_id",
        "dataset_image_artifact",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dataset_image_artifact_tenant_id", table_name="dataset_image_artifact"
    )
    op.drop_index(
        "ix_dataset_image_artifact_status_next", table_name="dataset_image_artifact"
    )
    op.drop_table("dataset_image_artifact")
