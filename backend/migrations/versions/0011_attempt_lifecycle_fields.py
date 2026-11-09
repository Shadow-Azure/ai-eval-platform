"""attempt lifecycle fields

Revision ID: 0011_attempt_lifecycle_fields
Revises: 0010_eval_run_queue_reason
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_attempt_lifecycle_fields"
down_revision = "0010_eval_run_queue_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand-only (design 05 §9): every column is nullable, so rolling back to
    # the previous image keeps reading the old schema.
    op.add_column("eval_run", sa.Column("github_run_id", sa.String(length=64), nullable=True))
    op.add_column(
        "eval_run",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "eval_case_attempt",
        sa.Column("phase_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("eval_case_attempt", "phase_changed_at")
    op.drop_column("eval_run", "cancel_requested_at")
    op.drop_column("eval_run", "github_run_id")
