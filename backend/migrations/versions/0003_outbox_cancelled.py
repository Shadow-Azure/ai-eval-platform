"""outbox gains a 'cancelled' status

Revision ID: 0003_outbox_cancelled
Revises: 0002_deployment
Create Date: 2026-11-09

M2-05 cancels pending shard dispatches when an evaluation is cancelled. The
row stays as reconciliation evidence (payload.last_error = run_cancelled)
instead of being deleted, so the outbox CHECK gains the 'cancelled' member.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_outbox_cancelled"
down_revision: str | None = "0002_deployment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CHECK = "status IN ('pending','processing','sent','failed')"
NEW_CHECK = "status IN ('pending','processing','sent','failed','cancelled')"


def upgrade() -> None:
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint(op.f("ck_outbox_status"), type_="check")
        batch.create_check_constraint("status", NEW_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint(op.f("ck_outbox_status"), type_="check")
        batch.create_check_constraint("status", OLD_CHECK)
