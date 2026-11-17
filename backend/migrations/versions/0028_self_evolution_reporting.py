"""self-evolution bad case reporting

Revision ID: 0028_self_evolution
Revises: 0027_agent_timeout
Create Date: 2026-11-17

M9 (#263): eval runs gain the optional self-evolution reporting target
(enabled flag, workspace base URL and the Fernet-encrypted workspace token).
The preparation row carries the same ciphertext between prepare and create so
the token never enters the JSON snapshot the wizard echoes back. Terminal runs
enqueue one ``self_evolution_report`` row per bad case; the worker POSTs the
frozen payload with ladder backoff. ``trace_session.native_session_id`` gains
the lookup index the signed session-envelope endpoint needs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_self_evolution"
down_revision: str | None = "0027_agent_timeout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine:
    """JSONB on PostgreSQL, portable JSON elsewhere (mirrors models.base)."""

    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "eval_run",
        sa.Column(
            "self_evolution_enabled", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column("eval_run", sa.Column("self_evolution_base_url", sa.String(length=512), nullable=True))
    op.add_column(
        "eval_run", sa.Column("self_evolution_workspace_id", sa.String(length=64), nullable=True)
    )
    op.add_column("eval_run", sa.Column("self_evolution_token_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "eval_preparation",
        sa.Column("self_evolution_token_encrypted", sa.Text(), nullable=True),
    )

    op.create_table(
        "self_evolution_report",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("eval_run_id", sa.String(length=36), nullable=False),
        sa.Column("eval_case_run_id", sa.String(length=36), nullable=False),
        sa.Column("bad_case_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_run_id"],
            ["eval_run.tenant_id", "eval_run.id"],
            name="fk_self_evolution_report_run_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_case_run_id"],
            ["eval_case_run.tenant_id", "eval_case_run.id"],
            name="fk_self_evolution_report_case_run_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "bad_case_id", name="uq_self_evolution_report_bad_case"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_self_evolution_report_tenant_id_id"),
        sa.CheckConstraint(
            "status IN ('pending','succeeded','failed')", name="status"
        ),
    )
    op.create_index(
        "ix_self_evolution_report_eval_run_id", "self_evolution_report", ["eval_run_id"]
    )
    op.create_index(
        "ix_self_evolution_report_eval_case_run_id",
        "self_evolution_report",
        ["eval_case_run_id"],
    )
    op.create_index(
        "ix_self_evolution_report_status_next",
        "self_evolution_report",
        ["status", "next_attempt_at"],
    )
    op.create_index("ix_self_evolution_report_tenant_id", "self_evolution_report", ["tenant_id"])

    op.create_index(
        "ix_trace_session_native_session_id", "trace_session", ["native_session_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_trace_session_native_session_id", table_name="trace_session")

    op.drop_index("ix_self_evolution_report_tenant_id", table_name="self_evolution_report")
    op.drop_index(
        "ix_self_evolution_report_status_next", table_name="self_evolution_report"
    )
    op.drop_index(
        "ix_self_evolution_report_eval_case_run_id", table_name="self_evolution_report"
    )
    op.drop_index("ix_self_evolution_report_eval_run_id", table_name="self_evolution_report")
    op.drop_table("self_evolution_report")

    op.drop_column("eval_preparation", "self_evolution_token_encrypted")
    op.drop_column("eval_run", "self_evolution_token_encrypted")
    op.drop_column("eval_run", "self_evolution_workspace_id")
    op.drop_column("eval_run", "self_evolution_base_url")
    op.drop_column("eval_run", "self_evolution_enabled")
