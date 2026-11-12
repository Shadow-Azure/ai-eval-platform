"""eval run prompt suffix

Revision ID: 0022_eval_run_prompt_suffix
Revises: 0021_trace_ingestion
Create Date: 2026-11-12

M8-07 (#193): an evaluation may append evaluation-level instructions to every
case problem statement. The normalized text is frozen in the preparation
snapshot and stored on the run; the empty default keeps today's behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_eval_run_prompt_suffix"
down_revision: str | None = "0021_trace_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_run",
        sa.Column("prompt_suffix", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("eval_run", "prompt_suffix")
