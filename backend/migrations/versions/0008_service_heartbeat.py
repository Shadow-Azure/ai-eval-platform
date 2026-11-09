"""service heartbeat for the administrator operations page

Revision ID: 0008_service_heartbeat
Revises: 0007_scoring
Create Date: 2026-11-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_service_heartbeat"
down_revision: str | None = "0007_scoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_heartbeat",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "detail_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_service_heartbeat")),
    )


def downgrade() -> None:
    op.drop_table("service_heartbeat")
