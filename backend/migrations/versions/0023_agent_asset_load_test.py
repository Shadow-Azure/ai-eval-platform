"""agent asset load test result

Revision ID: 0023_agent_asset_load_test
Revises: 0022_eval_run_prompt_suffix
Create Date: 2026-11-12

M8-10 (#202): CLI-generated agent sources (OpenSpec/CodeSpec) declare install
and init commands and prove them with an isolated load test. The result keeps
the status, log tail and generated asset manifest/contents so a resolve can
freeze them into the immutable asset bundle.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_agent_asset_load_test"
down_revision: str | None = "0022_eval_run_prompt_suffix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows need an object value; the server default also matches the
    # model's Python-side default=dict for every later insert.
    op.add_column(
        "agent_asset_source",
        sa.Column(
            "load_test_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_asset_source", "load_test_json")
