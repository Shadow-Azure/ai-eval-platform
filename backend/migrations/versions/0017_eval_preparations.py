"""expiring launch preparations

Revision ID: 0017_eval_preparations
Revises: 0016_agent_assets
Create Date: 2026-11-10

M5-04 (design 04 §3.1): the launch wizard confirms a server-rendered
preparation snapshot before creating a run. The snapshot freezes the dataset
case subset, asset commit, runtime, model credential, resource pool and
execution policy, carries a TTL, and is single-use. Expired and consumed rows
are kept for audit instead of deleted.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_eval_preparations"
down_revision: str | None = "0016_agent_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_preparation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_digest", sa.String(length=80), nullable=False),
        sa.Column("consumed_eval_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "consumed_eval_run_id"],
            ["eval_run.tenant_id", "eval_run.id"],
            name="fk_eval_preparation_run_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_eval_preparation_tenant_id_id"),
        sa.CheckConstraint(
            "status IN ('active','consumed','expired')", name="status"
        ),
    )
    op.create_index(
        "ix_eval_preparation_actor_status",
        "eval_preparation",
        ["tenant_id", "created_by", "status"],
    )
    op.create_index(
        "ix_eval_preparation_expiry",
        "eval_preparation",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_eval_preparation_expiry", table_name="eval_preparation")
    op.drop_index("ix_eval_preparation_actor_status", table_name="eval_preparation")
    op.drop_table("eval_preparation")
