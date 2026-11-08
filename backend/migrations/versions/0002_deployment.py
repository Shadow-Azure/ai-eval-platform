"""deployment records

Revision ID: 0002_deployment
Revises: 1a795994c4e9
Create Date: 2026-11-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_deployment"
down_revision: str | None = "1a795994c4e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployment",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=False),
        sa.Column("image_digest", sa.String(length=200), nullable=False),
        sa.Column("migration_revision", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("triggered_by", sa.String(length=128), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rollback_of_id", sa.String(length=36), nullable=True),
        sa.Column(
            "detail_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('deploying','succeeded','failed','rolled_back')",
            name=op.f("ck_deployment_status"),
        ),
        sa.ForeignKeyConstraint(
            ["rollback_of_id"],
            ["deployment.id"],
            name=op.f("fk_deployment_rollback_of_id_deployment"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment")),
    )
    op.create_index(
        "ix_deployment_environment_started",
        "deployment",
        ["environment", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_environment_started", table_name="deployment")
    op.drop_table("deployment")
