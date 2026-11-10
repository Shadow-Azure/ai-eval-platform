"""budget pause/resume fields

Revision ID: 0014_budget_pause_resume
Revises: 0013_resource_events
Create Date: 2026-11-10

M4-05: the evaluation row now stores the last reconciled budget spend, a
monotonic budget version guarding ``resume-budget`` idempotency, and the
reason a non-terminal pause was entered. Expand-only (design 05 §9): the
columns are nullable or carry a server default so an older image can keep
reading the table during a rolling deploy.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_budget_pause_resume"
down_revision: str | None = "0013_resource_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_run",
        sa.Column("budget_spent_usd", sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.add_column(
        "eval_run",
        sa.Column("budget_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "eval_run",
        sa.Column("pause_reason", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("eval_run", "pause_reason")
    op.drop_column("eval_run", "budget_version")
    op.drop_column("eval_run", "budget_spent_usd")
