"""dataset version and eval run agent timeout

Revision ID: 0027_agent_timeout
Revises: 0026_eval_run_max_turns
Create Date: 2026-11-15

M8-32 (#248): the Agent wall-clock timeout is configurable per dataset version
and overridable at launch. ``dataset_import`` carries the upload request value
to the materialize worker. The 1800s server defaults keep older versions/runs
on the previous 30-minute behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_agent_timeout"
down_revision: str | None = "0026_eval_run_max_turns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_version",
        sa.Column("agent_timeout_seconds", sa.Integer(), nullable=False, server_default="1800"),
    )
    op.add_column(
        "dataset_import",
        sa.Column("agent_timeout_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "eval_run",
        sa.Column("agent_timeout_seconds", sa.Integer(), nullable=False, server_default="1800"),
    )


def downgrade() -> None:
    op.drop_column("eval_run", "agent_timeout_seconds")
    op.drop_column("dataset_import", "agent_timeout_seconds")
    op.drop_column("dataset_version", "agent_timeout_seconds")
