"""template-level self-evolution bad case reporting

Revision ID: 0033_template_reporting
Revises: 0032_eval_template
Create Date: 2026-11-22

M8 (#382): ``eval_template`` carries the optional self-evolution reporting
target alongside the launch selection, mirroring the run-level configuration
(#263). The workspace token is stored as Fernet ciphertext so a template edit
and a one-click launch keep the same write-only contract: the API never returns
it, and a launch decrypts it only to freeze the run that reports bad cases.
Existing templates keep reporting disabled.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_template_reporting"
down_revision: str | None = "0032_eval_template"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_template",
        sa.Column(
            "self_evolution_enabled", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column(
        "eval_template",
        sa.Column("self_evolution_base_url", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "eval_template",
        sa.Column("self_evolution_workspace_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "eval_template",
        sa.Column("self_evolution_token_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("eval_template", "self_evolution_token_encrypted")
    op.drop_column("eval_template", "self_evolution_workspace_id")
    op.drop_column("eval_template", "self_evolution_base_url")
    op.drop_column("eval_template", "self_evolution_enabled")
