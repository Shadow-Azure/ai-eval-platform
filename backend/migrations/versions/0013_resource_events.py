"""resource event log

Revision ID: 0013_resource_events
Revises: 0012_resource_connections
Create Date: 2026-11-10

M4-03 B: one append-only table explains resource-pool capacity decisions.
The capacity controller records state changes, provisioning failures and
release failures here; ``dedupe_key`` (unique per tenant, nullable) keeps a
repeated worker tick with an unchanged state from spamming the log.

Events are scoped to a tenant and optionally to the pool/connection they
describe; deleting a pool or connection cascades to its events.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_resource_events"
down_revision: str | None = "0012_resource_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "resource_event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("resource_pool_id", sa.String(length=36), nullable=True),
        sa.Column("connection_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", JSON_TYPE, nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_resource_event_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "resource_pool_id"],
            ["resource_pool.tenant_id", "resource_pool.id"],
            name="fk_resource_event_pool_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["connection.tenant_id", "connection.id"],
            name="fk_resource_event_connection_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_event")),
        sa.UniqueConstraint(
            "tenant_id", "dedupe_key", name="uq_resource_event_tenant_dedupe"
        ),
    )
    op.create_index(
        op.f("ix_resource_event_tenant_id"),
        "resource_event",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_resource_event_pool_id_id",
        "resource_event",
        ["resource_pool_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_resource_event_pool_id_id", table_name="resource_event")
    op.drop_index(op.f("ix_resource_event_tenant_id"), table_name="resource_event")
    op.drop_table("resource_event")
