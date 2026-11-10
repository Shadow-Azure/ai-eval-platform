"""trace_status distinguishes incomplete and degraded capture

Revision ID: 0019_trace_capture_status
Revises: 0018_billing_cny
Create Date: 2026-11-10

M6-01: a runner may upload only stdout/stderr or a partial set of native
Claude Code files. The case must show that capture is not complete instead of
presenting a synthetic or partial trace as ready.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019_trace_capture_status"
down_revision: str | None = "0018_billing_cny"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_eval_case_run_trace_status"
OLD_VALUES = "'pending','ready','failed','unlinked'"
NEW_VALUES = "'pending','ready','incomplete','degraded','failed','unlinked'"


def upgrade() -> None:
    with op.batch_alter_table("eval_case_run") as batch:
        batch.drop_constraint(op.f(CONSTRAINT), type_="check")
        batch.create_check_constraint(op.f(CONSTRAINT), f"trace_status IN ({NEW_VALUES})")


def downgrade() -> None:
    op.execute(
        "UPDATE eval_case_run SET trace_status = 'ready' "
        "WHERE trace_status IN ('incomplete','degraded')"
    )
    with op.batch_alter_table("eval_case_run") as batch:
        batch.drop_constraint(op.f(CONSTRAINT), type_="check")
        batch.create_check_constraint(op.f(CONSTRAINT), f"trace_status IN ({OLD_VALUES})")
