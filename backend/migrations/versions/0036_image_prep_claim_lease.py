"""claim lease for the dataset image preparation queue

Revision ID: 0036_image_prep_claim_lease
Revises: 0035_service_token
Create Date: 2026-11-26

M8 (#436): a ``preparing`` row used to mean both "queued" and "a worker is
actively pulling it". ``claimed_at``/``lease_owner`` make the two states
distinguishable without a new status that would break existing clients:
``claimed_at IS NULL`` is queued, otherwise the row is processing. Both are
nullable so rows that predate this revision keep working (they are treated as
queued until the next claim).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_image_prep_claim_lease"
down_revision: str | None = "0035_service_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_image_artifact",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dataset_image_artifact",
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_image_artifact", "lease_owner")
    op.drop_column("dataset_image_artifact", "claimed_at")
