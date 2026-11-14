"""evaluation max turns

Revision ID: 0026_eval_run_max_turns
Revises: 0025_dataset_image_sources
Create Date: 2026-11-14

M8-31 (#246): an evaluation may cap the Claude Code agentic turns per case.
The value is optional at launch and defaults to 200; existing rows are
backfilled by the server default so older runs keep the previous behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_eval_run_max_turns"
down_revision: str | None = "0025_dataset_image_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_run",
        sa.Column("max_turns", sa.Integer(), nullable=False, server_default="200"),
    )


def downgrade() -> None:
    op.drop_column("eval_run", "max_turns")
