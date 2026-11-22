"""structured case failure detail

Revision ID: 0031_failure_detail
Revises: 0030_queue_progress_lease
Create Date: 2026-11-22

M8 (#375): the runner classifies the terminal Agent/model failure and the
platform persists the structured detail next to the legacy error code, so the
console, metrics and bad-case reporting can show the real upstream cause
(429 quota, auth, timeout, crash, asset install) instead of one node code.
Both columns are nullable so rows that predate this migration keep their old
error_code/error_summary display.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_failure_detail"
down_revision: str | None = "0030_queue_progress_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("eval_case_run", sa.Column("failure_detail_json", _JSON, nullable=True))
    op.add_column("eval_case_attempt", sa.Column("failure_detail_json", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("eval_case_attempt", "failure_detail_json")
    op.drop_column("eval_case_run", "failure_detail_json")
