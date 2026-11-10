"""resource connections and pool capacity policy

Revision ID: 0012_resource_connections
Revises: 0011_attempt_lifecycle_fields
Create Date: 2026-11-10

M4-03: a tenant-level ``connection`` records a resource credential (encrypted
``credential`` row with ``kind='resource'``), its provider/region, declared
capabilities and last verification result. ``resource_pool`` gains the
connection reference, provisioning mode, capacity bounds, instance parameters
and the price/capacity bookkeeping columns.

Existing pools backfill to ``fixed`` mode with a single-slot bound, matching
the fixed self-hosted runner behavior that predates spot provisioning
(design 05 §5, design 06 §6).

SQLite cannot ALTER in foreign keys or CHECK constraints, so the pool change
runs through a batch table recreate there; PostgreSQL gets plain ALTERs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_resource_connections"
down_revision: str | None = "0011_attempt_lifecycle_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

ADDED_POOL_COLUMNS = (
    "connection_id",
    "provisioning_mode",
    "min_capacity",
    "max_capacity",
    "instance_config_json",
    "price_threshold_usd",
    "price_usd",
    "capacity_reason",
    "last_checked_at",
    "next_check_at",
)

# Final names match the model's naming convention (ck_resource_pool_<name>).
PROVISIONING_MODE_CHECK = "ck_resource_pool_provisioning_mode"
CAPACITY_CHECK = "ck_resource_pool_capacity"
CONNECTION_FK = "fk_resource_pool_connection_tenant"


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _pool_columns() -> list[sa.Column]:
    return [
        sa.Column("connection_id", sa.String(length=36), nullable=True),
        sa.Column(
            "provisioning_mode",
            sa.String(length=32),
            server_default="fixed",
            nullable=False,
        ),
        sa.Column("min_capacity", sa.Integer(), server_default="0", nullable=False),
        # 50 is the design's example bound and keeps pre-M4-03 pools usable;
        # _backfill_legacy_max_capacity() restores a legacy labels override.
        sa.Column("max_capacity", sa.Integer(), server_default="50", nullable=False),
        sa.Column(
            "instance_config_json",
            JSON_TYPE,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("price_threshold_usd", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("price_usd", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("capacity_reason", sa.String(length=64), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _backfill_legacy_max_capacity() -> None:
    """Move a legacy ``labels_json.max_capacity`` into the real column (05 §5)."""

    if _is_sqlite():
        op.execute(
            "UPDATE resource_pool SET max_capacity = "
            "CAST(json_extract(labels_json, '$.max_capacity') AS INTEGER) "
            "WHERE json_extract(labels_json, '$.max_capacity') IS NOT NULL "
            "AND CAST(json_extract(labels_json, '$.max_capacity') AS INTEGER) > 0"
        )
    else:
        op.execute(
            "UPDATE resource_pool SET max_capacity = "
            "(labels_json ->> 'max_capacity')::int "
            "WHERE (labels_json ->> 'max_capacity') ~ '^[0-9]+$' "
            "AND (labels_json ->> 'max_capacity')::int > 0"
        )


def upgrade() -> None:
    op.create_table(
        "connection",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("credential_id", sa.String(length=36), nullable=False),
        sa.Column("capabilities_json", JSON_TYPE, nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="unverified", nullable=False
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('unverified','verified','failed')",
            name=op.f("ck_connection_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_connection_project_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["credential.tenant_id", "credential.id"],
            name="fk_connection_credential_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_connection_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connection")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_connection_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_connection_tenant_name"),
    )
    op.create_index(
        op.f("ix_connection_tenant_id"), "connection", ["tenant_id"], unique=False
    )

    if _is_sqlite():
        with op.batch_alter_table("resource_pool") as batch_op:
            for column in _pool_columns():
                batch_op.add_column(column)
            batch_op.create_foreign_key(
                CONNECTION_FK,
                "connection",
                ["tenant_id", "connection_id"],
                ["tenant_id", "id"],
                ondelete="RESTRICT",
            )
            batch_op.create_check_constraint(
                op.f(PROVISIONING_MODE_CHECK), "provisioning_mode IN ('fixed','spot')"
            )
            batch_op.create_check_constraint(
                op.f(CAPACITY_CHECK),
                "max_capacity >= 1 AND min_capacity >= 0 AND min_capacity <= max_capacity",
            )
    else:
        for column in _pool_columns():
            op.add_column("resource_pool", column)
        op.create_foreign_key(
            CONNECTION_FK,
            "resource_pool",
            "connection",
            ["tenant_id", "connection_id"],
            ["tenant_id", "id"],
            ondelete="RESTRICT",
        )
        op.create_check_constraint(
            op.f(PROVISIONING_MODE_CHECK),
            "resource_pool",
            "provisioning_mode IN ('fixed','spot')",
        )
        op.create_check_constraint(
            op.f(CAPACITY_CHECK),
            "resource_pool",
            "max_capacity >= 1 AND min_capacity >= 0 AND min_capacity <= max_capacity",
        )
    _backfill_legacy_max_capacity()


def downgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table("resource_pool") as batch_op:
            batch_op.drop_constraint(op.f(CAPACITY_CHECK), type_="check")
            batch_op.drop_constraint(op.f(PROVISIONING_MODE_CHECK), type_="check")
            batch_op.drop_constraint(CONNECTION_FK, type_="foreignkey")
            for name in reversed(ADDED_POOL_COLUMNS):
                batch_op.drop_column(name)
    else:
        op.drop_constraint(op.f(CAPACITY_CHECK), "resource_pool", type_="check")
        op.drop_constraint(op.f(PROVISIONING_MODE_CHECK), "resource_pool", type_="check")
        op.drop_constraint(CONNECTION_FK, "resource_pool", type_="foreignkey")
        for name in reversed(ADDED_POOL_COLUMNS):
            op.drop_column("resource_pool", name)

    op.drop_index(op.f("ix_connection_tenant_id"), table_name="connection")
    op.drop_table("connection")
