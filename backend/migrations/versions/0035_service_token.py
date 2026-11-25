"""service tokens for machine-triggered template launches

Revision ID: 0035_service_token
Revises: 0034_pool_unique_labels
Create Date: 2026-11-25

M10 (#429): harness-evolution cannot hold a browser session, so the platform
gains a machine identity. ``service_token`` stores only the SHA-256 hex of a
``hev_`` bearer token (the plaintext is returned once at creation), together
with its scopes, an optional template allowlist and optional concurrency/budget
caps. The two new ``eval_run`` columns freeze the candidate asset override and
the evolution lineage on the run itself; both default to ``{}`` so every run
created before this revision keeps the console's one-click semantics.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_service_token"
down_revision: str | None = "0034_pool_unique_labels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "service_token",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes_json", _JSON, nullable=False),
        sa.Column("template_allowlist_json", _JSON, nullable=False),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=True),
        sa.Column("daily_budget_cny", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name="fk_service_token_tenant_id_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_service_token_tenant_name"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_service_token_tenant_id_id"),
        sa.UniqueConstraint("token_hash", name="uq_service_token_token_hash"),
    )
    op.create_index("ix_service_token_tenant_id", "service_token", ["tenant_id"])
    op.create_index(
        "ix_service_token_tenant_revoked_created",
        "service_token",
        ["tenant_id", "revoked_at", "created_at"],
    )

    op.add_column(
        "eval_run",
        sa.Column(
            "asset_override_json",
            _JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "eval_run",
        sa.Column(
            "evolution_lineage_json",
            _JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("eval_run", "evolution_lineage_json")
    op.drop_column("eval_run", "asset_override_json")
    op.drop_index("ix_service_token_tenant_revoked_created", table_name="service_token")
    op.drop_index("ix_service_token_tenant_id", table_name="service_token")
    op.drop_table("service_token")
