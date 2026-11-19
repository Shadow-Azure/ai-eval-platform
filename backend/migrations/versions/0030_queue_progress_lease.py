"""shard progress lease for the platform queue timeout

Revision ID: 0030_queue_progress_lease
Revises: 0029_dataset_image_artifacts
Create Date: 2026-11-19

M8-45 (#307): a shard is dispatched in one shot but runs in several waves. The
fixed ``eval_queue_timeout_seconds`` wall clock killed every attempt that never
started within an hour. ``queue_started_at`` records when an attempt first
entered ``queued`` (the shard's absolute 23h ceiling is measured from the
earliest one) and ``queue_deadline`` is the progress lease the reaper reads.
Both are nullable: rows that predate this migration keep the old fallback in
the reaper, so an in-flight upgrade does not mass-time-out queued attempts.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_queue_progress_lease"
down_revision: str | None = "0029_dataset_image_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_case_attempt",
        sa.Column("queue_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "eval_case_attempt",
        sa.Column("queue_deadline", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("eval_case_attempt", "queue_deadline")
    op.drop_column("eval_case_attempt", "queue_started_at")
