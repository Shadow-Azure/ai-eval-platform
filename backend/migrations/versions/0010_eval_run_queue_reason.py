"""eval_run queue reason

Revision ID: 0010_eval_run_queue_reason
Revises: 0009_dataset_environment_image
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_eval_run_queue_reason"
down_revision = "0009_dataset_environment_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand-only: the column is nullable so a rollback to the previous image
    # keeps reading the old schema (design 05 §3).
    op.add_column("eval_run", sa.Column("queue_reason", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_run", "queue_reason")
